import csv, json, time
from pathlib import Path
from csv_consistency_repair import repair_bundle
from csv_consistency_repair.io import read_table
try:
    import pandas as pd
except Exception:
    pd = None
ROOT=Path(__file__).resolve().parents[1]/'benchmark_evidence'; D=ROOT/'bundle_corpus'; O=ROOT/'bundle_outputs'; D.mkdir(exist_ok=True); O.mkdir(exist_ok=True)
def write(p,h,rows):
    with p.open('w',newline='') as f: w=csv.writer(f); w.writerow(h); w.writerows(rows)
res=[]
for k in range(5):
    b=D/f'b{k}'; b.mkdir(exist_ok=True); outdir=O/f'b{k}'; outdir.mkdir(exist_ok=True)
    customers=[[f'C{i}',f'Name{i}','CA' if i%2 else 'US'] for i in range(1,21)]; write(b/'customers.csv',['customer_id','name','country'],customers)
    orders=[]; clean=[]
    for i in range(1,101):
        cid=f'C{1+(i+k)%20}'; country='CA' if int(cid[1:])%2 else 'US'; sub=10+(i%17); tax=2+(i%3); total=sub+tax; meters=1+(i%5)
        row=[f'O{i}',cid,country,str(sub),str(tax),str(total),f'{meters} m']; clean.append(row[:]); orders.append(row[:])
    for i in [10,30,50,70,90]: orders[i][2]='XX'
    for i in [12,32,52,72,92]: orders[i][5]='999'
    for i in [14,34,54,74,94]: orders[i][6]=f'{(1+(i+1)%5)*100} cm'
    write(b/'orders.csv',['order_id','customer_id','country','subtotal','tax','total','length'],orders); write(b/'orders.clean.csv',['order_id','customer_id','country','subtotal','tax','total','length'],clean)
    rules={'functional_dependencies':[{'determinant':['customer_id'],'dependent':'country','min_support':3,'min_confidence':0.66,'repair':True}], 'row_formulas':[{'target':'total','expression':{'op':'sum','columns':['subtotal','tax']},'tolerance':0,'repair':True}], 'units':[{'column':'length','canonical':'m','aliases':{'cm':0.01},'repair':True}], 'foreign_keys':[{'column':'customer_id','reference_file':'customers.csv','reference_column':'customer_id'}]}
    (b/'orders.rules.json').write_text(json.dumps(rules)); (b/'bundle.json').write_text(json.dumps({'datasets':[{'name':'customers','input':'customers.csv'},{'name':'orders','input':'orders.csv','rules':'orders.rules.json','output':'orders.repaired.csv'}]}))
    t=time.perf_counter(); rr=repair_bundle(b/'bundle.json',outdir,outdir/'bundle.report.json'); tool_s=time.perf_counter()-t
    tool_exact=read_table(outdir/'orders.repaired.csv').logical_digest()==read_table(b/'orders.clean.csv').logical_digest()
    if pd is None:
        pan_exact=None; pan_s=None
    else:
        t=time.perf_counter(); od=pd.read_csv(b/'orders.csv',dtype=str); cu=pd.read_csv(b/'customers.csv',dtype=str); mp=dict(zip(cu.customer_id,cu.country)); od['country']=od['customer_id'].map(mp); od['total']=(pd.to_numeric(od['subtotal'])+pd.to_numeric(od['tax'])).map(lambda x:str(int(x)) if float(x).is_integer() else str(x))
        def conv(x):
            x=str(x).strip(); return f'{float(x[:-3])*0.01:g} m' if x.endswith(' cm') else x
        od['length']=od['length'].map(conv); p=outdir/'pandas.orders.csv'; od.to_csv(p,index=False); pan_s=time.perf_counter()-t; pan_exact=read_table(p).logical_digest()==read_table(b/'orders.clean.csv').logical_digest()
    res.append({'case':k,'tool_exact':tool_exact,'tool_seconds':tool_s,'tool_edits':rr['committed_edits'],'tool_atomic_commit':rr.get('transaction_state'),'pandas_available':pd is not None,'pandas_exact':pan_exact,'pandas_seconds':pan_s}); print(res[-1],flush=True)
summary={'cases':5,'tool_exact_rate':sum(x['tool_exact'] for x in res)/5,'pandas_available':pd is not None,'pandas_configured_exact_rate':(sum(bool(x['pandas_exact']) for x in res)/5 if pd is not None else None),'tool_mean_s':sum(x['tool_seconds'] for x in res)/5,'pandas_mean_s':(sum(x['pandas_seconds'] for x in res)/5 if pd is not None else None)}
(ROOT/'MULTIFILE_BUNDLE_RESULTS.json').write_text(json.dumps({'summary':summary,'cases':res},indent=2)+"\n"); print(summary)
