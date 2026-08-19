from __future__ import annotations

from pathlib import Path
import csv

from csv_consistency_repair import repair


def test_wide_batch_does_not_starve_independent_repairs(tmp_path: Path):
    src=tmp_path/'in.csv'; out=tmp_path/'out.csv'; rep=tmp_path/'rep.json'
    with src.open('w',newline='',encoding='utf-8') as f:
        w=csv.writer(f)
        w.writerow([' id ',' name ','note'])
        for i in range(20):
            w.writerow([f' {i} ',f' N{i} ','alpha'])
    result=repair(src,out,rep)
    with out.open(newline='',encoding='utf-8') as f:
        rows=list(csv.reader(f))
    assert rows[0]==['id','name','note']
    assert rows[1:]==[[str(i),f'N{i}','alpha'] for i in range(20)]
    assert result.final_status=='PASS'
    assert result.report['closure']['forward_replay_pass'] is True
    assert result.report['closure']['inverse_roundtrip_pass'] is True
    assert result.committed_edits==42
