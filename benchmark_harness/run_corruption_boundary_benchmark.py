import csv, json, random, tempfile, time
from pathlib import Path
from csv_consistency_repair import repair, RepairConfig
from csv_consistency_repair.io import read_table
ROOT=Path(__file__).resolve().parents[1]/'benchmark_evidence'; res=[]
for pct in [5,10,20,25,30,31,35,40]:
    for seed in range(3):
        rng=random.Random(9000+pct*17+seed); n=100
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'x.csv'; c=Path(td)/'c.csv'; o=Path(td)/'o.csv'; r=Path(td)/'r.json'; rows=[]
            for i in range(1,n+1):
                q=2+i%7; pr=3+i%11; sub=q*pr; fee=2+i%3; tot=sub+fee; rows.append([str(i),str(q),str(pr),str(sub),str(fee),str(tot)])
            dirty=[x[:] for x in rows]; idx=rng.sample(range(n),pct)
            for j in idx: dirty[j][3]=str(int(dirty[j][3])+7)
            for path,data in [(p,dirty),(c,rows)]:
                with path.open('w',newline='') as f: w=csv.writer(f); w.writerow(['id','q','p','sub','fee','total']); w.writerows(data)
            t=time.perf_counter(); rr=repair(p,o,r,RepairConfig(discover_numeric_constraints=True,repair_numeric_constraints=True,max_cycles=8,auto_mode=False)); dt=time.perf_counter()-t
            A=read_table(o); C=read_table(c); fixed=sum(A.rows[j][3]==C.rows[j][3] for j in idx); wrong=sum(A.rows[j]!=C.rows[j] for j in range(n) if j not in idx); res.append({'pct':pct,'seed':seed,'fixed':fixed,'recall':fixed/pct,'wrong_clean_rows':wrong,'edits':rr.committed_edits,'seconds':dt})
summary=[]
for p in [5,10,20,25,30,31,35,40]:
    xs=[x for x in res if x['pct']==p]; summary.append({'pct':p,'mean_recall':sum(x['recall'] for x in xs)/3,'max_wrong_clean_rows':max(x['wrong_clean_rows'] for x in xs),'mean_seconds':sum(x['seconds'] for x in xs)/3}); print(p,summary[-1],flush=True)
(ROOT/'CORRUPTION_THRESHOLD_CURRENT.json').write_text(json.dumps({'summary':summary,'runs':res},indent=2)+"\n")
