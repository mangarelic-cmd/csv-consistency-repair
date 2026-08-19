import csv, json, tempfile, time
from pathlib import Path
from csv_consistency_repair import repair, RepairConfig
from csv_consistency_repair.io import read_table
ROOT=Path(__file__).resolve().parents[1]/'benchmark_evidence'; res=[]
for n in [48,400,800,2000]:
    with tempfile.TemporaryDirectory() as td:
        p=Path(td)/'x.csv'; o=Path(td)/'o.csv'; r=Path(td)/'r.json'; c=Path(td)/'clean.csv'; rows=[]
        for i in range(1,n+1):
            q=2+i%7; pr=3+i%11; sub=q*pr; fee=2+i%3; tot=sub+fee; rows.append([str(i),str(q),str(pr),str(sub),str(fee),str(tot)])
        dirty=[x[:] for x in rows]; dirty[n//2][3]=str(int(dirty[n//2][3])+7)
        for path,data in [(p,dirty),(c,rows)]:
            with path.open('w',newline='') as f: w=csv.writer(f); w.writerow(['id','q','p','sub','fee','total']); w.writerows(data)
        t=time.perf_counter(); rr=repair(p,o,r,RepairConfig(safe_mode=True)); dt=time.perf_counter()-t; exact=read_table(o).logical_digest()==read_table(c).logical_digest(); res.append({'rows':n,'seconds':dt,'edits':rr.committed_edits,'exact':exact,'status':rr.final_status,'forward':rr.report['closure']['forward_replay_pass'],'inverse':rr.report['closure']['inverse_roundtrip_pass']}); print(res[-1],flush=True)
(ROOT/'SAFE_MODE_SCALING_CURRENT.json').write_text(json.dumps(res,indent=2)+"\n")
