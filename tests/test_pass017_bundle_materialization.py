from pathlib import Path
import json

from csv_consistency_repair.bundle_materialization import (
    discover_bundle_repair_proposals,
    materialize_bundle_repairs,
)
from csv_consistency_repair.models import Table
from csv_consistency_repair import repair_bundle, undo_bundle
from csv_consistency_repair.io import read_table


def test_cross_file_attribute_materializes_only_after_repeated_agreement():
    parent = Table(header=['customer_id','country'], rows=[[f'C{i}', 'CA' if i%2 else 'US'] for i in range(1,21)])
    child = Table(header=['order_id','customer_id','country'], rows=[])
    for i in range(100):
        cid=f'C{1+(i%20)}'; country='CA' if int(cid[1:])%2 else 'US'
        child.rows.append([f'O{i}', cid, country])
    child.rows[10][2]='XX'
    child.rows[30][2]=''
    props=discover_bundle_repair_proposals({'parent':parent,'child':child})
    targets={(p['row'],p['new_value']) for p in props if p['file']=='child' and p['column']==2}
    assert (10,'CA' if int(child.rows[10][1][1:])%2 else 'US') in targets
    assert (30,'CA' if int(child.rows[30][1][1:])%2 else 'US') in targets
    result=materialize_bundle_repairs({'parent':parent,'child':child})
    assert result['applied_count'] >= 2
    out=result['tables']['child']
    assert out.rows[10][2] == ('CA' if int(out.rows[10][1][1:])%2 else 'US')
    assert out.rows[30][2] == ('CA' if int(out.rows[30][1][1:])%2 else 'US')
    assert result['materialization_credit'] == 0
    assert result['repair_credit'] >= 2
    assert result['post_apply_remeasure_pass'] is True


def test_bundle_materialization_is_reversible(tmp_path: Path):
    parent=tmp_path/'customers.csv'; child=tmp_path/'orders.csv'; manifest=tmp_path/'bundle.json'; out=tmp_path/'out'
    parent.write_text('customer_id,country\nC1,CA\nC2,US\nC3,CA\nC4,US\nC5,CA\nC6,US\nC7,CA\nC8,US\n', encoding='utf-8')
    rows=['order_id,customer_id,country']
    for i in range(40):
        cid=f'C{1+(i%8)}'; country='CA' if int(cid[1:])%2 else 'US'
        if i==10: country='XX'
        rows.append(f'O{i},{cid},{country}')
    child.write_text('\n'.join(rows)+'\n', encoding='utf-8')
    manifest.write_text(json.dumps({'datasets':[{'name':'customers','input':'customers.csv'},{'name':'orders','input':'orders.csv'}]}), encoding='utf-8')
    summary=repair_bundle(manifest,out,out/'bundle.report.json')
    assert summary['bundle_materialization']['applied_count'] >= 1
    repaired=read_table(out/'orders.repaired.csv')
    assert repaired.rows[10][2] == ('CA' if int(repaired.rows[10][1][1:])%2 else 'US')
    restored=undo_bundle(out/'bundle.report.json',out/'restored')
    assert restored['all_roundtrip_pass'] is True
    assert read_table(out/'restored'/'orders.restored.csv').logical_digest() == read_table(child).logical_digest()
