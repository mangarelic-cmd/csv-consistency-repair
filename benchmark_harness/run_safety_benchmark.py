import csv, json, random, time
from pathlib import Path
from csv_consistency_repair import repair, RepairConfig
from csv_consistency_repair.io import read_table
ROOT=Path(__file__).resolve().parents[1]/'benchmark_evidence'; D=ROOT/'safety_corpus'; O=ROOT/'safety_outputs'; D.mkdir(exist_ok=True); O.mkdir(exist_ok=True)
rng=random.Random(99123); res=[]
def write(p,h,rows):
    with p.open('w',newline='') as f: w=csv.writer(f); w.writerow(h); w.writerows(rows)
for k in range(30):
    h=['id','group','a','b','c','status','note']; rows=[]
    for i in range(1,21):
        g='A' if i<=10 else 'B'; a=i+k+3; b=2*a+(1 if (k%3==0 and i==7) else 0); c=(a+b if g=='A' else a-b); status=['ok','hold','review'][i%3]; note='<5' if i%11==0 else ('N/A' if i%13==0 else f'free {i}|x;z'); rows.append([str(i),g,str(a),str(b),str(c),status,note])
    if k%2==0: rows[15][4]=str(int(rows[15][4])+17)
    p=D/f's{k}.csv'; out=O/f's{k}.out.csv'; rep=O/f's{k}.json'; write(p,h,rows); truth=read_table(p).logical_digest(); t=time.perf_counter(); rr=repair(p,out,rep,RepairConfig(safe_mode=True)); dt=time.perf_counter()-t; got=read_table(out).logical_digest(); res.append({'id':k,'unchanged':truth==got,'edits':rr.committed_edits,'seconds':dt,'status':rr.final_status}); print(k,res[-1],flush=True)
summary={'cases':len(res),'unchanged':sum(x['unchanged'] for x in res),'mutation_cases':sum(not x['unchanged'] for x in res),'total_edits':sum(x['edits'] for x in res),'mean_s':sum(x['seconds'] for x in res)/len(res)}
(ROOT/'SAFETY_STRESS_RESULTS.json').write_text(json.dumps({'summary':summary,'cases':res},indent=2)+"\n"); print(summary)
