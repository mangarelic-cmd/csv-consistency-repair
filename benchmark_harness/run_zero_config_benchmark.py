import json, statistics, time
from pathlib import Path
from csv_consistency_repair import repair, RepairConfig
from csv_consistency_repair.io import read_table

ROOT = Path(__file__).resolve().parents[1] / "benchmark_evidence"
LOCK = json.loads((ROOT / "FUNCTIONAL_MANIFEST_LOCK.json").read_text())
OUT = ROOT / "zero_outputs"; OUT.mkdir(exist_ok=True)
def resolve(path):
    p=Path(path); return p if p.is_absolute() else ROOT/p
def cells(t):
    x={(-1,j):v for j,v in enumerate(t.header)}
    for i,row in enumerate(t.rows):
        for j,v in enumerate(row): x[(i,j)]=v
    return x
runs=[]; TB=TOUCH=GOOD=REPAIRED=FALSE=0; exact=0
for c in LOCK['cases']:
    dirty, cleanp = resolve(c['dirty']), resolve(c['clean'])
    d=read_table(dirty); clean=read_table(cleanp); out=OUT/(c['id']+'.csv'); rep=OUT/(c['id']+'.json')
    t=time.perf_counter(); rr=repair(dirty,out,rep,RepairConfig()); dt=time.perf_counter()-t; r=read_table(out)
    dm,rm,cm=cells(d),cells(r),cells(clean); ks=set(dm)|set(rm)|set(cm)
    bad={k for k in ks if dm.get(k)!=cm.get(k)}; touch={k for k in ks if dm.get(k)!=rm.get(k)}
    good={k for k in touch if k in bad and rm.get(k)==cm.get(k)}; repaired={k for k in bad if rm.get(k)==cm.get(k)}
    false={k for k in touch if k not in bad or rm.get(k)!=cm.get(k)}; ex=r.logical_digest()==clean.logical_digest()
    exact+=ex; TB+=len(bad); TOUCH+=len(touch); GOOD+=len(good); REPAIRED+=len(repaired); FALSE+=len(false)
    runs.append({'id':c['id'],'family':c['family'],'truth_bad':len(bad),'touched':len(touch),'good_touches':len(good),'repaired_truth':len(repaired),'false_mutations':len(false),'exact':ex,'seconds':dt,'edits':rr.committed_edits,'status':rr.final_status})
    print(c['id'],ex,len(repaired),len(bad),len(false),round(dt,3),flush=True)
summary={'cases':len(runs),'truth_bad':TB,'touched':TOUCH,'good_touches':GOOD,'repaired_truth':REPAIRED,'false_mutations':FALSE,'precision_micro':GOOD/TOUCH if TOUCH else 1.0,'recall_micro':REPAIRED/TB if TB else 1.0,'exact_datasets':exact,'exact_rate':exact/len(runs),'median_s':statistics.median(x['seconds'] for x in runs),'total_s':sum(x['seconds'] for x in runs)}
(ROOT/'ZERO_CONFIG_COMPETITION_RESULTS.json').write_text(json.dumps({'summary':summary,'runs':runs},indent=2)+"\n"); print(json.dumps(summary,indent=2))
