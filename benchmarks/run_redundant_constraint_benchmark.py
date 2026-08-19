from __future__ import annotations
from pathlib import Path
import json, random, tempfile, time
from csv_consistency_repair import RepairConfig, repair
from csv_consistency_repair.io import read_table

rng=random.Random(20260819)
with tempfile.TemporaryDirectory() as td:
    td=Path(td); src=td/'ledger.csv'; out=td/'fixed.csv'; rep=td/'report.json'
    rows=['row_id,qty,price,subtotal,tax,total']
    bad=[]
    for i in range(5000):
        qty=rng.randint(1,20); price=rng.randint(2,200); subtotal=qty*price; tax=rng.randint(0,30); total=subtotal+tax
        if i%53==7:
            subtotal += 17; bad.append(i)
        rows.append(f'R{i:06d},{qty},{price},{subtotal},{tax},{total}')
    src.write_text('\n'.join(rows)+'\n',encoding='utf-8')
    cfg=RepairConfig(discover_numeric_constraints=True,repair_numeric_constraints=True,discovery_confidence=0.97,discovery_min_rows=100)
    t=time.perf_counter(); result=repair(src,out,rep,cfg); seconds=time.perf_counter()-t
    table=read_table(out)
    exact=all(int(row[3])==int(row[1])*int(row[2]) for row in table.rows)
    edits=[e for e in result.report['committed_edits'] if e['metadata'].get('rule_type')=='redundant_numeric_constraint_consensus']
    proof={
      'rows':5000,'corruptions':len(bad),'repaired_by_consensus':len(edits),'exact_ground_truth_reconstruction':exact,
      'stable_numeric_constraints':result.report['numeric_constraint_discovery']['stable_relations'],
      'final_status':result.final_status,'forward_replay_pass':result.report['closure']['forward_replay_pass'],
      'inverse_roundtrip_pass':result.report['closure']['inverse_roundtrip_pass'],'seconds':seconds
    }
    print(json.dumps(proof,indent=2))
