from __future__ import annotations

import csv
import json
from pathlib import Path

from csv_consistency_repair import RepairConfig, repair
from csv_consistency_repair.cli import build_parser
from csv_consistency_repair.io import read_table
from csv_consistency_repair.streaming import StreamRepairConfig, stream_repair, stream_undo


def _write(path: Path, header, rows):
    with path.open('w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def test_auto_mode_repairs_redundant_numeric_corruption_and_can_be_disabled(tmp_path: Path):
    clean=[]
    for i in range(1,33):
        q=2+i%7; price=3+(i*2)%11; subtotal=q*price; fee=2+i%3; total=subtotal+fee
        clean.append([str(i),str(q),str(price),str(subtotal),str(fee),str(total)])
    dirty=[r[:] for r in clean]
    dirty[16][3]=str(int(dirty[16][3])+7)
    src=tmp_path/'in.csv'; out=tmp_path/'out.csv'; rep=tmp_path/'rep.json'
    _write(src,['id','q','price','subtotal','fee','total'],dirty)
    rr=repair(src,out,rep,RepairConfig())
    assert read_table(out).rows == clean
    assert rr.report['closure']['forward_replay_pass'] is True
    assert rr.report['closure']['inverse_roundtrip_pass'] is True

    out2=tmp_path/'out2.csv'; rep2=tmp_path/'rep2.json'
    repair(src,out2,rep2,RepairConfig(auto_mode=False))
    assert read_table(out2).rows[16][3] == dirty[16][3]


def test_sparse_corruption_consensus_repairs_twenty_percent_without_clean_mutation(tmp_path: Path):
    clean=[]
    for i in range(1,101):
        q=2+i%7; price=3+i%11; subtotal=q*price; fee=2+i%3; total=subtotal+fee
        clean.append([str(i),str(q),str(price),str(subtotal),str(fee),str(total)])
    dirty=[r[:] for r in clean]
    bad=set(range(0,100,5))  # exactly 20%
    for j in bad:
        dirty[j][3]=str(int(dirty[j][3])+7)
    src=tmp_path/'in.csv'; out=tmp_path/'out.csv'; rep=tmp_path/'rep.json'
    _write(src,['id','q','price','subtotal','fee','total'],dirty)
    rr=repair(src,out,rep,RepairConfig(discover_numeric_constraints=True,repair_numeric_constraints=True,max_cycles=8,auto_mode=False))
    got=read_table(out).rows
    assert got == clean
    assert rr.committed_edits == len(bad)


def test_row_alignment_can_reconstruct_uniquely_missing_numeric_cell(tmp_path: Path):
    clean=[[str(i),f'Name{i}',str(10+i),f'X{i%3}'] for i in range(1,23)]
    dirty=[r[:] for r in clean]
    # Missing value shifts the remaining code one position to the left.
    dirty[9]=[dirty[9][0],dirty[9][1],dirty[9][3]]
    src=tmp_path/'in.csv'; out=tmp_path/'out.csv'; rep=tmp_path/'rep.json'
    _write(src,['id','name','value','code'],dirty)
    rr=repair(src,out,rep,RepairConfig(maxima50=True,maxima_repair=True,maxima_repair_row_alignment=True,max_cycles=6,auto_mode=False))
    assert read_table(out).rows == clean
    assert rr.report['closure']['forward_replay_pass'] is True
    assert rr.report['closure']['inverse_roundtrip_pass'] is True


def test_stream_replay_and_undo_roundtrip_after_fast_path_changes(tmp_path: Path):
    src=tmp_path/'in.csv'; out=tmp_path/'out.csv'; rep=tmp_path/'rep.json'; restored=tmp_path/'restored.csv'
    rows=[[str(i), ' YES ' if i%2 else ' no ', f' text {i} '] for i in range(1,101)]
    _write(src,['id','flag','text'],rows)
    r=stream_repair(src,out,rep,StreamRepairConfig(normalize_booleans=True,journal_edits=True,verify_replay=True))
    assert r['replay_pass'] is True
    assert r['rows_read'] == 100
    u=stream_undo(out,rep,restored)
    assert u['logical_roundtrip_pass'] is True
    assert read_table(restored).logical_digest() == read_table(src).logical_digest()


def test_cli_exposes_no_auto_switch():
    args=build_parser().parse_args(['input.csv','-o','out.csv','--report','r.json','--no-auto'])
    assert args.no_auto is True


def test_safe_mode_does_not_double_count_numeric_mapping_as_independent_witness(tmp_path: Path):
    clean=[]
    for i in range(1,401):
        q=2+i%7; price=3+i%11; subtotal=q*price; fee=2+i%3; total=subtotal+fee
        clean.append([str(i),str(q),str(price),str(subtotal),str(fee),str(total)])
    dirty=[r[:] for r in clean]
    dirty[200][3]=str(int(dirty[200][3])+7)
    src=tmp_path/'in.csv'; out=tmp_path/'out.csv'; rep=tmp_path/'rep.json'
    _write(src,['id','q','price','subtotal','fee','total'],dirty)
    rr=repair(src,out,rep,RepairConfig(safe_mode=True))
    assert read_table(out).rows == clean
    assert rr.report['closure']['forward_replay_pass'] is True
    assert rr.report['closure']['inverse_roundtrip_pass'] is True
