import csv, json, time
from pathlib import Path
from csv_consistency_repair import repair, RepairConfig
from csv_consistency_repair.io import read_table
try:
    import pandas as pd
except Exception:
    pd = None
ROOT=Path(__file__).resolve().parents[1]/'benchmark_evidence'; D=ROOT/'format_corpus'; O=ROOT/'format_outputs'; D.mkdir(exist_ok=True); O.mkdir(exist_ok=True)
results=[]; delims=[',',';','\t','|']; lts=['\n','\r\n']
for i in range(160):
    delim=delims[i%4]; lt=lts[(i//4)%2]; bom=(i%7==0); p=D/f'f{i}.csv'; out=O/f'f{i}.out.csv'; rep=O/f'f{i}.json'
    rows=[[str(r),f'alpha{delim}inside-{i}-{r}',f'quote "Q{r}"',f'multi\nline {r}' if r%5==0 else f'plain {r}',f'Ω-{i}-{r}'] for r in range(12)]
    with p.open('w',encoding='utf-8-sig' if bom else 'utf-8',newline='') as f:
        w=csv.writer(f,delimiter=delim,lineterminator=lt,quoting=csv.QUOTE_MINIMAL); w.writerow(['id','embedded','quoted','multiline','unicode']); w.writerows(rows)
    truth=read_table(p); t=time.perf_counter()
    try:
        rr=repair(p,out,rep,RepairConfig(max_cycles=3,stable_cycles_required=2)); dt=time.perf_counter()-t; got=read_table(out)
        tool={'ok':True,'seconds':dt,'logical_pass':got.logical_digest()==truth.logical_digest(),'edits':rr.committed_edits,'format_preservation':rr.report.get('closure',{}).get('format_preservation_pass')}
    except Exception as e: tool={'ok':False,'seconds':time.perf_counter()-t,'error':repr(e)}
    if pd is None:
        pandas={'available':False,'ok':False,'logical_pass':False,'seconds':0.0}
    else:
        pt=time.perf_counter()
        try:
            df=pd.read_csv(p,dtype=str,keep_default_na=False,sep=None,engine='python'); pdrows=[[str(x) for x in row] for row in df.itertuples(index=False,name=None)]
            pandas={'available':True,'ok':True,'seconds':time.perf_counter()-pt,'logical_pass':list(df.columns)==truth.header and pdrows==truth.rows}
        except Exception as e: pandas={'available':True,'ok':False,'seconds':time.perf_counter()-pt,'logical_pass':False,'error':repr(e)}
    results.append({'id':i,'delimiter':repr(delim),'lineterminator':repr(lt),'bom':bom,'tool':tool,'pandas':pandas})
summary={'cases':len(results),'tool_parse_success':sum(x['tool'].get('ok',False) for x in results),'tool_logical_roundtrip':sum(x['tool'].get('logical_pass',False) for x in results),'tool_zero_edit':sum(x['tool'].get('edits')==0 for x in results),'tool_format_preservation':sum(x['tool'].get('format_preservation') is True for x in results),'pandas_available':pd is not None,'pandas_parse_success':sum(x['pandas'].get('ok',False) for x in results),'pandas_logical_parse':sum(x['pandas'].get('logical_pass',False) for x in results),'tool_total_s':sum(x['tool']['seconds'] for x in results),'pandas_total_s':sum(x['pandas']['seconds'] for x in results)}
(ROOT/'FORMAT_ROBUSTNESS_RESULTS.json').write_text(json.dumps({'summary':summary,'cases':results},indent=2)+"\n",encoding='utf-8'); print(json.dumps(summary,indent=2))
