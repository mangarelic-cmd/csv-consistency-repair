from __future__ import annotations

from ._version import __version__
"""Bounded-memory CSV repair path for local/canonicalization workloads.

This path intentionally does not attempt global formula discovery.  It is designed for
large files where the requested operations are local and exact: outer-whitespace repair,
optional null/boolean normalization, and optional exact deduplication.  Exact duplicate
state is stored in SQLite so process RAM stays bounded by parser buffers and small column
profiles.
"""

from dataclasses import dataclass, asdict
from hashlib import sha256
from pathlib import Path
from typing import Any
import csv
import io
import json
import os
import sqlite3
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor

from .io import _detect_delimiter

NULL_MARKERS = {"na", "n/a", "null", "none", "nil", "missing"}
TRUE_WORDS = {"true", "yes", "y", "t"}
FALSE_WORDS = {"false", "no", "n", "f"}
NULL_MARKERS_B = {x.encode('ascii') for x in NULL_MARKERS}
TRUE_WORDS_B = {x.encode('ascii') for x in TRUE_WORDS}
FALSE_WORDS_B = {x.encode('ascii') for x in FALSE_WORDS}
UTF8_BOM = b"\xef\xbb\xbf"


class _HashingReader(io.RawIOBase):
    def __init__(self, raw):
        self.raw = raw
        self.h = sha256()
    def readable(self): return True
    def readinto(self, b):
        n = self.raw.readinto(b)
        if n:
            self.h.update(memoryview(b)[:n])
        return n
    def close(self):
        try: self.raw.close()
        finally: super().close()


class _HashingWriter(io.RawIOBase):
    def __init__(self, raw):
        self.raw = raw
        self.h = sha256()
    def writable(self): return True
    def write(self, b):
        if b:
            self.h.update(b)
        return self.raw.write(b)
    def flush(self):
        self.raw.flush()
    def close(self):
        try: self.raw.close()
        finally: super().close()


@dataclass
class StreamRepairConfig:
    trim_outer_whitespace: bool = True
    normalize_null_markers: bool = False
    normalize_booleans: bool = False
    remove_exact_duplicates: bool = False
    verify_replay: bool = True
    journal_edits: bool = True
    boolean_profile_rows: int = 50000
    parallel_workers: int = 0
    parallel_min_bytes: int = 64 * 1024 * 1024


def _file_sha(path: Path) -> str:
    h=sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()


def _lineterminator(raw: bytes) -> str:
    if b'\r\n' in raw: return '\r\n'
    if b'\r' in raw and b'\n' not in raw: return '\r'
    return '\n'


def _canonical_cell(v: str, *, boolish: bool, cfg: StreamRepairConfig) -> tuple[str, str | None]:
    original=v
    if cfg.trim_outer_whitespace:
        v=v.strip()
    if cfg.normalize_null_markers and v.casefold() in NULL_MARKERS:
        v=''
    if cfg.normalize_booleans and boolish:
        low=v.casefold()
        if low in TRUE_WORDS: v='true'
        elif low in FALSE_WORDS: v='false'
    return v, (None if v==original else original)


def _boolish_columns(path: Path, delimiter: str, width: int, cfg: StreamRepairConfig) -> list[bool]:
    """Conservatively infer boolean columns from a bounded prefix.

    Only explicit boolean tokens are ever rewritten.  Requiring an essentially pure prefix
    avoids an extra full-file profiling pass on multi-million-row files.
    """
    total=[0]*width; bools=[0]*width
    limit=max(1000,int(getattr(cfg,'boolean_profile_rows',50000)))
    with path.open('r',encoding='utf-8-sig',newline='') as f:
        rd=csv.reader(f,delimiter=delimiter,strict=True)
        try: next(rd)
        except StopIteration: return [False]*width
        for ri,row in enumerate(rd):
            if ri>=limit: break
            for c in range(min(width,len(row))):
                v=row[c].strip()
                if not v: continue
                if cfg.normalize_null_markers and v.casefold() in NULL_MARKERS: continue
                total[c]+=1
                if v.casefold() in TRUE_WORDS|FALSE_WORDS: bools[c]+=1
    return [(t>=2 and b/t>=.995) for b,t in zip(bools,total)]


def _logical_update(h, row: list[str]) -> None:
    # Collision-free length-prefixed row digest. Build one row packet and perform one
    # hash update instead of 2N+1 tiny updates; this matters materially at 10M+ rows.
    packet=bytearray(len(row).to_bytes(4,'little',signed=False))
    for field in row:
        b=field.encode('utf-8')
        packet.extend(len(b).to_bytes(8,'little',signed=False)); packet.extend(b)
    h.update(packet)


def _header_probe(path: Path) -> tuple[str,list[str],bool,str]:
    with path.open('rb') as f:
        raw=f.read(65536)
    bom=raw.startswith(UTF8_BOM)
    text=raw.decode('utf-8-sig',errors='strict')
    delimiter=_detect_delimiter(text)
    lt=_lineterminator(raw)
    with path.open('r',encoding='utf-8-sig',newline='') as f:
        rd=csv.reader(f,delimiter=delimiter,quotechar='"',doublequote=True,strict=True)
        try: header=list(next(rd))
        except StopIteration: header=[]
    return delimiter,header,bom,lt


class _SimpleCSVFallback(Exception):
    pass


def _logical_update_bytes(h, row: list[bytes]) -> None:
    packet=bytearray(len(row).to_bytes(4,'little',signed=False))
    for b in row:
        packet.extend(len(b).to_bytes(8,'little',signed=False)); packet.extend(b)
    h.update(packet)


def _canonical_cell_bytes(v: bytes, *, boolish: bool, cfg: StreamRepairConfig) -> bytes:
    if cfg.trim_outer_whitespace:
        v=v.strip()
    low=v.lower()
    if cfg.normalize_null_markers and low in NULL_MARKERS_B:
        return b''
    if cfg.normalize_booleans and boolish:
        if low in TRUE_WORDS_B: return b'true'
        if low in FALSE_WORDS_B: return b'false'
    return v


def _strip_line_ending(line: bytes, lt: bytes) -> bytes:
    if lt and line.endswith(lt): return line[:-len(lt)]
    if line.endswith(b'\n'): return line[:-1]
    if line.endswith(b'\r'): return line[:-1]
    return line



def _simple_chunk_worker(args) -> dict[str, Any]:
    path_s,start,end,data_start,delimiter,width,boolish,lt,cfg,out_s,write_output=args
    path=Path(path_s); d=delimiter.encode('ascii'); ltb=lt.encode('ascii')
    hin=sha256(); hout=sha256(); rows=0; edits=0; fallback=False
    fo=None; buffer=bytearray()
    try:
        if write_output:
            fo=Path(out_s).open('wb',buffering=1024*1024)
        with path.open('rb',buffering=1024*1024) as fi:
            fi.seek(start)
            if start>data_start:
                fi.seek(start-1); prev=fi.read(1); fi.seek(start)
                if prev!=b'\n': fi.readline()
            while True:
                pos=fi.tell()
                if pos>=end: break
                line=fi.readline()
                if not line: break
                body=_strip_line_ending(line,ltb)
                if b'"' in body:
                    fallback=True; break
                fields=body.split(d)
                if len(fields)!=width:
                    fallback=True; break
                _logical_update_bytes(hin,fields)
                out=[]
                for c,v in enumerate(fields):
                    nv=_canonical_cell_bytes(v,boolish=(c<len(boolish) and boolish[c]),cfg=cfg)
                    edits += int(nv!=v); out.append(nv)
                _logical_update_bytes(hout,out); rows+=1
                if fo is not None:
                    buffer.extend(d.join(out)+ltb)
                    if len(buffer)>=1024*1024:
                        fo.write(buffer); buffer.clear()
            if fo is not None and buffer: fo.write(buffer)
    finally:
        if fo is not None: fo.close()
    return {'rows':rows,'edits':edits,'input_digest':hin.hexdigest(),'output_digest':hout.hexdigest(),'fallback':fallback,'out':out_s}


def _chunk_merkle_digest(header: list[bytes], chunks: list[dict[str,Any]], field: str) -> str:
    h=sha256(); _logical_update_bytes(h,header)
    for i,x in enumerate(chunks):
        h.update(i.to_bytes(4,'little')); h.update(int(x['rows']).to_bytes(8,'little')); h.update(bytes.fromhex(x[field]))
    return h.hexdigest()


def _parallel_simple_transform(path: Path, out_path: Path, delimiter: str, width: int, boolish: list[bool], bom: bool, lt: str, cfg: StreamRepairConfig, *, replay_only: bool=False) -> dict[str,Any] | None:
    if cfg.remove_exact_duplicates or cfg.journal_edits or lt not in ('\n','\r\n'):
        return None
    workers=int(cfg.parallel_workers or min(4, os.cpu_count() or 1))
    if workers<=1 or path.stat().st_size < int(cfg.parallel_min_bytes):
        return None
    d=delimiter.encode('ascii')
    if len(d)!=1: return None
    with path.open('rb') as f:
        first=f.readline()
    body=_strip_line_ending(first,lt.encode('ascii'))
    if bom and body.startswith(UTF8_BOM): body=body[len(UTF8_BOM):]
    if b'"' in body: return None
    header=body.split(d)
    if len(header)!=width: return None
    out_header=[_canonical_cell_bytes(v,boolish=False,cfg=cfg) for v in header]
    header_edits=sum(a!=b for a,b in zip(header,out_header))
    data_start=len(first); size=path.stat().st_size; span=max(0,size-data_start)
    tmpdir=Path(tempfile.mkdtemp(prefix='csv-repair-parallel-'))
    args=[]
    for i in range(workers):
        st=data_start+(span*i)//workers; en=data_start+(span*(i+1))//workers
        out_s=str(tmpdir/f'chunk-{i:03d}.csv')
        args.append((str(path),st,en,data_start,delimiter,width,boolish,lt,cfg,out_s,not replay_only))
    try:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            chunks=list(ex.map(_simple_chunk_worker,args))
        if any(x['fallback'] for x in chunks): return None
        result={
            'rows_read':sum(x['rows'] for x in chunks),'rows_written':sum(x['rows'] for x in chunks),
            'edits':header_edits+sum(x['edits'] for x in chunks),
            'input_logical_digest':_chunk_merkle_digest(header,chunks,'input_digest'),
            'output_logical_digest':_chunk_merkle_digest(out_header,chunks,'output_digest'),
            'logical_digest_format':'chunk-merkle-v1','chunks':workers,
        }
        if replay_only: return result
        tmp=out_path.with_suffix(out_path.suffix+'.parallel.tmp')
        with tmp.open('wb',buffering=1024*1024) as fo:
            if bom: fo.write(UTF8_BOM)
            fo.write(d.join(out_header)+lt.encode('ascii'))
            for x in chunks:
                with Path(x['out']).open('rb',buffering=1024*1024) as fi:
                    while True:
                        b=fi.read(4*1024*1024)
                        if not b: break
                        fo.write(b)
        os.replace(tmp,out_path)
        result['input_sha256']=_file_sha(path); result['output_sha256']=_file_sha(out_path)
        return result
    finally:
        import shutil
        shutil.rmtree(tmpdir,ignore_errors=True)


def _try_simple_fast_transform(path: Path, out_path: Path, delimiter: str, width: int, boolish: list[bool], bom: bool, lt: str, cfg: StreamRepairConfig) -> dict[str, Any] | None:
    """Binary fast path for quote-free, fixed-width CSV.

    The path validates every row while streaming.  Encountering quotes or a width change
    aborts the attempt and falls back to the fully general RFC-style parser from byte zero.
    """
    if cfg.remove_exact_duplicates or cfg.journal_edits:
        return None
    d=delimiter.encode('ascii'); ltb=lt.encode('ascii')
    if len(d)!=1:
        return None
    inp_sha=sha256(); out_sha=sha256(); in_log=sha256(); out_log=sha256()
    rows=0; edits=0; tmp=out_path.with_suffix(out_path.suffix+'.simple.tmp')
    try:
        with path.open('rb',buffering=1024*1024) as fi, tmp.open('wb',buffering=1024*1024) as fo:
            if bom:
                fo.write(UTF8_BOM); out_sha.update(UTF8_BOM)
            buffer=bytearray()
            for ri,line in enumerate(fi):
                inp_sha.update(line)
                body=_strip_line_ending(line,ltb)
                if ri==0 and bom and body.startswith(UTF8_BOM): body=body[len(UTF8_BOM):]
                if b'"' in body:
                    raise _SimpleCSVFallback
                fields=body.split(d)
                if len(fields)!=width:
                    raise _SimpleCSVFallback
                _logical_update_bytes(in_log,fields)
                out=[]
                for c,v in enumerate(fields):
                    nv=_canonical_cell_bytes(v,boolish=(ri>0 and c<len(boolish) and boolish[c]),cfg=cfg)
                    edits += int(nv!=v); out.append(nv)
                _logical_update_bytes(out_log,out)
                encoded=d.join(out)+ltb
                buffer.extend(encoded)
                if len(buffer)>=1024*1024:
                    fo.write(buffer); out_sha.update(buffer); buffer.clear()
                if ri>0: rows+=1
            if buffer:
                fo.write(buffer); out_sha.update(buffer)
        os.replace(tmp,out_path)
        return {'rows_read':rows,'rows_written':rows,'edits':edits,'input_sha256':inp_sha.hexdigest(),'output_sha256':out_sha.hexdigest(),'input_logical_digest':in_log.hexdigest(),'output_logical_digest':out_log.hexdigest()}
    except _SimpleCSVFallback:
        try: tmp.unlink()
        except FileNotFoundError: pass
        return None


def _simple_replay_digest(path: Path, delimiter: str, width: int, boolish: list[bool], bom: bool, lt: str, cfg: StreamRepairConfig) -> str | None:
    d=delimiter.encode('ascii'); ltb=lt.encode('ascii'); h=sha256()
    if len(d)!=1: return None
    with path.open('rb',buffering=1024*1024) as fi:
        for ri,line in enumerate(fi):
            body=_strip_line_ending(line,ltb)
            if ri==0 and bom and body.startswith(UTF8_BOM): body=body[len(UTF8_BOM):]
            if b'"' in body: return None
            fields=body.split(d)
            if len(fields)!=width: return None
            out=[_canonical_cell_bytes(v,boolish=(ri>0 and c<len(boolish) and boolish[c]),cfg=cfg) for c,v in enumerate(fields)]
            _logical_update_bytes(h,out)
    return h.hexdigest()


def _transformed_logical_digest(path: Path, delimiter: str, width: int, boolish: list[bool], cfg: StreamRepairConfig) -> str:
    """Cold deterministic replay without materializing a second output file."""
    h=sha256(); conn=None; dbpath=None
    try:
        if cfg.remove_exact_duplicates:
            fd,dbpath=tempfile.mkstemp(prefix='csv-repair-replay-seen-',suffix='.sqlite3'); os.close(fd)
            conn=sqlite3.connect(dbpath); conn.execute('CREATE TABLE seen (row_json TEXT PRIMARY KEY)')
        with path.open('r',encoding='utf-8-sig',newline='') as f:
            rd=csv.reader(f,delimiter=delimiter,quotechar='"',doublequote=True,strict=True)
            try: header=list(next(rd))
            except StopIteration: return h.hexdigest()
            out_header=[_canonical_cell(v,boolish=False,cfg=cfg)[0] for v in header]
            _logical_update(h,out_header)
            for row in rd:
                out=[_canonical_cell(v,boolish=(c<len(boolish) and boolish[c]),cfg=cfg)[0] for c,v in enumerate(row)]
                if conn is not None:
                    payload=json.dumps(out,ensure_ascii=False,separators=(',',':'))
                    try: conn.execute('INSERT INTO seen(row_json) VALUES(?)',(payload,))
                    except sqlite3.IntegrityError: continue
                _logical_update(h,out)
        return h.hexdigest()
    finally:
        if conn is not None: conn.close()
        if dbpath:
            try: Path(dbpath).unlink()
            except FileNotFoundError: pass


def stream_repair(
    input_path: str | Path,
    output_path: str | Path,
    report_path: str | Path | None = None,
    config: StreamRepairConfig | None = None,
) -> dict[str, Any]:
    cfg=config or StreamRepairConfig()
    input_path=Path(input_path); output_path=Path(output_path)
    delimiter,header,bom,lt=_header_probe(input_path); width=len(header)
    boolish=_boolish_columns(input_path,delimiter,width,cfg) if cfg.normalize_booleans else [False]*width
    output_path.parent.mkdir(parents=True,exist_ok=True)
    started=time.perf_counter()

    parallel=_parallel_simple_transform(input_path,output_path,delimiter,width,boolish,bom,lt,cfg)
    if parallel is not None:
        replay_pass=None
        if cfg.verify_replay:
            replay=_parallel_simple_transform(input_path,output_path,delimiter,width,boolish,bom,lt,StreamRepairConfig(
                trim_outer_whitespace=cfg.trim_outer_whitespace,normalize_null_markers=cfg.normalize_null_markers,
                normalize_booleans=cfg.normalize_booleans,remove_exact_duplicates=False,verify_replay=False,journal_edits=False,
                boolean_profile_rows=cfg.boolean_profile_rows,parallel_workers=cfg.parallel_workers,parallel_min_bytes=cfg.parallel_min_bytes),replay_only=True)
            replay_pass=bool(replay and replay['output_logical_digest']==parallel['output_logical_digest'])
        elapsed=time.perf_counter()-started
        result={
            'tool':'csv-consistency-repair','version':__version__,'mode':'bounded_memory_stream_repair',
            'input':str(input_path),'output':str(output_path),'config':asdict(cfg),
            'input_sha256':parallel['input_sha256'],'output_sha256':parallel['output_sha256'],
            'input_logical_digest':parallel['input_logical_digest'],'output_logical_digest':parallel['output_logical_digest'],'logical_digest_format':parallel['logical_digest_format'],
            'format_contract':{'delimiter':delimiter,'lineterminator':lt,'utf8_bom':bom,'encoding':'utf-8'},
            'rows_read':parallel['rows_read'],'rows_written':parallel['rows_written'],'columns':width,
            'edits':parallel['edits'],'duplicates_removed':0,'row_width_mismatches':0,'bounded_memory':True,
            'sqlite_duplicate_index':False,'sqlite_edit_ledger':False,'replay_pass':replay_pass,'replay_mode':'parallel_chunk_reexecution',
            'journal':None,'seconds':elapsed,'rows_per_second':parallel['rows_read']/max(elapsed,1e-12),
            'fast_simple_csv':True,'parallel_workers':parallel['chunks'],'feature_ids':[99,107,108,109,110,111,120],
        }
        if report_path:
            Path(report_path).write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
        return result

    fast=_try_simple_fast_transform(input_path,output_path,delimiter,width,boolish,bom,lt,cfg)
    if fast is not None:
        replay_pass=None
        if cfg.verify_replay:
            rd=_simple_replay_digest(input_path,delimiter,width,boolish,bom,lt,StreamRepairConfig(
                trim_outer_whitespace=cfg.trim_outer_whitespace,normalize_null_markers=cfg.normalize_null_markers,
                normalize_booleans=cfg.normalize_booleans,remove_exact_duplicates=False,verify_replay=False,
                journal_edits=False,boolean_profile_rows=cfg.boolean_profile_rows))
            replay_pass=(rd==fast['output_logical_digest'])
        elapsed=time.perf_counter()-started
        result={
            'tool':'csv-consistency-repair','version':__version__,'mode':'bounded_memory_stream_repair',
            'input':str(input_path),'output':str(output_path),'config':asdict(cfg),
            'input_sha256':fast['input_sha256'],'output_sha256':fast['output_sha256'],
            'input_logical_digest':fast['input_logical_digest'],'output_logical_digest':fast['output_logical_digest'],'logical_digest_format':'lenprefix-v1',
            'format_contract':{'delimiter':delimiter,'lineterminator':lt,'utf8_bom':bom,'encoding':'utf-8'},
            'rows_read':fast['rows_read'],'rows_written':fast['rows_written'],'columns':width,
            'edits':fast['edits'],'duplicates_removed':0,'row_width_mismatches':0,'bounded_memory':True,
            'sqlite_duplicate_index':False,'sqlite_edit_ledger':False,'replay_pass':replay_pass,'replay_mode':'cold_digest_reexecution',
            'journal':None,'seconds':elapsed,'rows_per_second':fast['rows_read']/max(elapsed,1e-12),
            'fast_simple_csv':True,'feature_ids':[99,107,108,109,110,111,120],
        }
        if report_path:
            Path(report_path).write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
        return result

    # SQLite is now used only for the operation that actually needs an out-of-core set:
    # exact deduplication.  The previous implementation inserted every edit into SQLite
    # even when journaling was disabled, dominating the 10M-row benchmark.
    conn=None; dbpath=None
    if cfg.remove_exact_duplicates:
        fd,dbpath=tempfile.mkstemp(prefix='csv-repair-stream-',suffix='.sqlite3'); os.close(fd)
        conn=sqlite3.connect(dbpath)
        conn.execute('PRAGMA synchronous=OFF'); conn.execute('PRAGMA journal_mode=OFF')
        conn.execute('CREATE TABLE seen (row_json TEXT PRIMARY KEY)')

    journal_path=str(output_path)+'.undo.jsonl' if cfg.journal_edits else None
    jf=Path(journal_path).open('w',encoding='utf-8') if journal_path else None
    def journal(rec: dict[str,Any]) -> None:
        if jf is not None:
            jf.write(json.dumps(rec,ensure_ascii=False,separators=(',',':'))+'\n')

    edits=0; duplicates=0; mismatches=0; input_rows=0; output_rows=0
    tmp=output_path.with_suffix(output_path.suffix+'.tmp')
    input_logical=sha256(); output_logical=sha256()
    try:
        # Hash the exact input and output bytes while parsing/writing them.  This avoids
        # two extra full-file SHA passes on multi-million-row streams.
        fi_raw=input_path.open('rb'); hi=_HashingReader(fi_raw); fi_buf=io.BufferedReader(hi,buffer_size=1024*1024); fi=io.TextIOWrapper(fi_buf,encoding='utf-8-sig',newline='')
        fo_raw=tmp.open('wb'); ho=_HashingWriter(fo_raw)
        if bom: ho.write(UTF8_BOM)
        fo_buf=io.BufferedWriter(ho,buffer_size=1024*1024); fo=io.TextIOWrapper(fo_buf,encoding='utf-8',newline='')
        try:
            rd=csv.reader(fi,delimiter=delimiter,quotechar='"',doublequote=True,strict=True)
            wr=csv.writer(fo,delimiter=delimiter,quotechar='"',doublequote=True,lineterminator=lt)
            try: in_header=list(next(rd))
            except StopIteration: in_header=[]
            if in_header: _logical_update(input_logical,in_header)
            out_header=[]
            for c,v in enumerate(in_header):
                nv,old=_canonical_cell(v,boolish=False,cfg=cfg); out_header.append(nv)
                if old is not None:
                    journal({'input_row':-1,'output_row':-1,'column':c,'old':old,'new':nv,'operation':'set_header','row':None}); edits+=1
            if in_header:
                wr.writerow(out_header); _logical_update(output_logical,out_header)
            for r,row in enumerate(rd):
                input_rows+=1; _logical_update(input_logical,list(row))
                if len(row)!=width: mismatches+=1
                out=[]
                for c,v in enumerate(row):
                    nv,old=_canonical_cell(v,boolish=(c<len(boolish) and boolish[c]),cfg=cfg); out.append(nv)
                    if old is not None:
                        journal({'input_row':r,'output_row':output_rows,'column':c,'old':old,'new':nv,'operation':'set_cell','row':None}); edits+=1
                if conn is not None:
                    payload=json.dumps(out,ensure_ascii=False,separators=(',',':'))
                    try: conn.execute('INSERT INTO seen(row_json) VALUES(?)',(payload,))
                    except sqlite3.IntegrityError:
                        journal({'input_row':r,'output_row':output_rows,'column':None,'old':None,'new':None,'operation':'delete_row','row':list(row)})
                        duplicates+=1; edits+=1; continue
                wr.writerow(out); _logical_update(output_logical,out); output_rows+=1
            fo.flush(); fo_buf.flush(); ho.flush()
            input_sha=hi.h.hexdigest(); output_sha=ho.h.hexdigest()
        finally:
            try: fi.close()
            except Exception: pass
            try: fo.close()
            except Exception: pass
        os.replace(tmp,output_path)
        if conn is not None: conn.commit()
        if jf is not None: jf.flush(); jf.close(); jf=None

        input_logical_digest=input_logical.hexdigest(); output_logical_digest=output_logical.hexdigest()
        replay_pass=None
        if cfg.verify_replay:
            replay_pass=_transformed_logical_digest(input_path,delimiter,width,boolish,StreamRepairConfig(
                trim_outer_whitespace=cfg.trim_outer_whitespace,
                normalize_null_markers=cfg.normalize_null_markers,
                normalize_booleans=cfg.normalize_booleans,
                remove_exact_duplicates=cfg.remove_exact_duplicates,
                verify_replay=False,
                journal_edits=False,
                boolean_profile_rows=cfg.boolean_profile_rows,
            )) == output_logical_digest

        elapsed=time.perf_counter()-started
        result={
            'tool':'csv-consistency-repair','version':__version__,'mode':'bounded_memory_stream_repair',
            'input':str(input_path),'output':str(output_path),'config':asdict(cfg),
            'input_sha256':input_sha,'output_sha256':output_sha,
            'input_logical_digest':input_logical_digest,'output_logical_digest':output_logical_digest,'logical_digest_format':'lenprefix-v1',
            'format_contract':{'delimiter':delimiter,'lineterminator':lt,'utf8_bom':bom,'encoding':'utf-8'},
            'rows_read':input_rows,'rows_written':output_rows,'columns':width,
            'edits':edits,'duplicates_removed':duplicates,'row_width_mismatches':mismatches,
            'bounded_memory':True,'sqlite_duplicate_index':bool(cfg.remove_exact_duplicates),
            'sqlite_edit_ledger':False,'replay_pass':replay_pass,'replay_mode':'cold_digest_reexecution',
            'journal':journal_path,'seconds':elapsed,
            'rows_per_second': input_rows/max(elapsed,1e-12),
            'feature_ids':[99,107,108,109,110,111,120],
        }
        if report_path:
            Path(report_path).write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
        return result
    finally:
        if jf is not None: jf.close()
        if conn is not None: conn.close()
        if dbpath:
            try: Path(dbpath).unlink()
            except FileNotFoundError: pass
        try: tmp.unlink()
        except FileNotFoundError: pass


def _stream_logical_digest(path: Path, delimiter: str) -> str:
    h=sha256()
    with path.open('r',encoding='utf-8-sig',newline='') as f:
        rd=csv.reader(f,delimiter=delimiter,quotechar='"',doublequote=True,strict=True)
        for row in rd:
            _logical_update(h,list(row))
    return h.hexdigest()


def stream_undo(repaired_path: str | Path, report_path: str | Path, output_path: str | Path) -> dict[str, Any]:
    """Undo a bounded-memory stream repair using its JSONL journal."""
    repaired_path=Path(repaired_path); report_path=Path(report_path); output_path=Path(output_path)
    report=json.loads(report_path.read_text(encoding='utf-8'))
    journal=report.get('journal')
    if not journal or not Path(journal).exists():
        raise ValueError('Streaming repair journal is missing.')
    fmt=report.get('format_contract',{})
    delimiter=fmt.get('delimiter',','); lt=fmt.get('lineterminator','\n'); bom=bool(fmt.get('utf8_bom'))
    fd,dbpath=tempfile.mkstemp(prefix='csv-repair-stream-undo-',suffix='.sqlite3');os.close(fd)
    conn=sqlite3.connect(dbpath)
    conn.execute('CREATE TABLE edits (input_row INTEGER, column_no INTEGER, old TEXT, new TEXT, operation TEXT, row_json TEXT)')
    with Path(journal).open('r',encoding='utf-8') as jf:
        for line in jf:
            if not line.strip(): continue
            x=json.loads(line)
            conn.execute('INSERT INTO edits VALUES(?,?,?,?,?,?)',(x.get('input_row'),x.get('column'),x.get('old'),x.get('new'),x.get('operation'),json.dumps(x.get('row'),ensure_ascii=False,separators=(',',':')) if x.get('row') is not None else None))
    conn.execute('CREATE INDEX idx_edits_row ON edits(input_row)');conn.commit()
    tmp=output_path.with_suffix(output_path.suffix+'.tmp'); output_path.parent.mkdir(parents=True,exist_ok=True)
    try:
        with repaired_path.open('r',encoding='utf-8-sig',newline='') as fi, tmp.open('w',encoding='utf-8',newline='') as fo:
            rd=csv.reader(fi,delimiter=delimiter,quotechar='"',doublequote=True,strict=True)
            wr=csv.writer(fo,delimiter=delimiter,quotechar='"',doublequote=True,lineterminator=lt)
            try: header=next(rd)
            except StopIteration: header=[]
            for c,old in conn.execute("SELECT column_no,old FROM edits WHERE input_row=-1 AND operation='set_header' ORDER BY column_no"):
                if c is not None and c<len(header): header[c]=old
            if header: wr.writerow(header)
            it=iter(rd)
            for r in range(int(report.get('rows_read',0))):
                deleted=conn.execute("SELECT row_json FROM edits WHERE input_row=? AND operation='delete_row' LIMIT 1",(r,)).fetchone()
                if deleted:
                    wr.writerow(json.loads(deleted[0])); continue
                try: row=list(next(it))
                except StopIteration: raise ValueError('Repaired stream ended before journal reconstruction completed.')
                for c,old in conn.execute("SELECT column_no,old FROM edits WHERE input_row=? AND operation='set_cell' ORDER BY column_no",(r,)):
                    if c is not None and c<len(row): row[c]=old
                wr.writerow(row)
        if bom:
            data=tmp.read_bytes();tmp.write_bytes(UTF8_BOM+data)
        os.replace(tmp,output_path)
        logical_pass=_stream_logical_digest(output_path,delimiter)==report.get('input_logical_digest')
        return {'output':str(output_path),'logical_roundtrip_pass':logical_pass,'output_sha256':_file_sha(output_path),'feature_ids':[111,119,120]}
    finally:
        conn.close()
        try:Path(dbpath).unlink()
        except FileNotFoundError:pass
        try:tmp.unlink()
        except FileNotFoundError:pass
