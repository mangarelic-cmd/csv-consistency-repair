from pathlib import Path
import csv

from csv_consistency_repair import RepairConfig, repair
from csv_consistency_repair.io import read_table
from csv_consistency_repair.scope import discover_scoped_relations
from csv_consistency_repair.sequential import discover_sequential_constraints


def write_csv(path: Path, header, rows):
    with path.open('w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def test_group_scoped_formula_projects_missing_values(tmp_path):
    p = tmp_path/'scoped.csv'; out = tmp_path/'out.csv'; rep = tmp_path/'rep.json'
    rows = []
    for mode, k in [('A',2),('B',3)]:
        for x in range(1,21):
            rows.append([mode, str(x), '' if x == 11 else str(k*x)])
    write_csv(p, ['mode','x','y'], rows)
    cfg = RepairConfig(discover_scoped_relations=True, repair_missing_values=True, repair_scoped_missing=True, scope_min_rows=8)
    result = repair(p, out, rep, cfg)
    tab = read_table(out)
    assert tab.rows[10][2] == '22'
    assert tab.rows[30][2] == '33'
    assert result.report['scope_discovery']['stable_relations'] >= 2
    assert result.report['closure']['forward_replay_pass'] is True
    assert result.report['closure']['inverse_roundtrip_pass'] is True


def test_group_scoped_formula_does_not_project_outside_learned_range(tmp_path):
    p = tmp_path/'ood.csv'; out = tmp_path/'out.csv'; rep = tmp_path/'rep.json'
    rows = []
    for mode, k in [('A',2),('B',3)]:
        for x in range(1,21):
            rows.append([mode, str(x), str(k*x)])
    rows.append(['A','999',''])
    write_csv(p, ['mode','x','y'], rows)
    cfg = RepairConfig(discover_scoped_relations=True, repair_missing_values=True, repair_scoped_missing=True, scope_min_rows=8)
    repair(p, out, rep, cfg)
    assert read_table(out).rows[-1][2] == ''


def test_row_segment_change_point_projects_in_each_segment(tmp_path):
    p = tmp_path/'segments.csv'; out = tmp_path/'out.csv'; rep = tmp_path/'rep.json'
    rows=[]
    for i in range(1,31):
        rows.append([str(i), '' if i == 15 else str(2*i)])
    for i in range(31,61):
        rows.append([str(i), '' if i == 45 else str(3*i)])
    write_csv(p, ['x','y'], rows)
    cfg=RepairConfig(discover_scoped_relations=True, repair_missing_values=True, repair_scoped_missing=True, scope_min_rows=8)
    result=repair(p,out,rep,cfg)
    tab=read_table(out)
    assert tab.rows[14][1]=='30'
    assert tab.rows[44][1]=='135'
    assert result.report['scope_discovery']['row_segment_relation_count'] >= 1


def test_sequential_two_sided_reconstructs_missing_balance(tmp_path):
    p=tmp_path/'seq.csv'; out=tmp_path/'out.csv'; rep=tmp_path/'rep.json'
    rows=[]
    bal=100
    rows.append(['0',str(bal),'0','0'])
    for i in range(1,31):
        inc=10+i; outv=i%4; bal=bal+inc-outv
        rows.append([str(i), '' if i==15 else str(bal), str(inc), str(outv)])
    expected=str(100 + sum((10+i)-(i%4) for i in range(1,16)))
    write_csv(p,['step','balance','credit','debit'],rows)
    cfg=RepairConfig(discover_sequential_constraints=True, repair_missing_values=True, repair_sequential_missing=True, sequential_min_rows=10)
    result=repair(p,out,rep,cfg)
    assert read_table(out).rows[15][1]==expected
    assert result.report['sequential_constraint_discovery']['stable_relations'] >= 1


def test_sequential_two_sided_repairs_corrupted_balance(tmp_path):
    p=tmp_path/'seqbad.csv'; out=tmp_path/'out.csv'; rep=tmp_path/'rep.json'
    rows=[]; bal=50; truth={0:bal}
    rows.append(['0',str(bal),'0','0'])
    for i in range(1,28):
        inc=5+i; outv=i%3; bal=bal+inc-outv; truth[i]=bal
        rows.append([str(i), str(99999 if i==14 else bal), str(inc), str(outv)])
    write_csv(p,['step','balance','credit','debit'],rows)
    cfg=RepairConfig(discover_sequential_constraints=True, repair_sequential_values=True, sequential_min_rows=10, sequential_confidence=0.95)
    result=repair(p,out,rep,cfg)
    assert read_table(out).rows[14][1]==str(truth[14])
    assert any(e['metadata'].get('rule_type')=='two_sided_sequential_reconstruction' for e in result.report['committed_edits'])


def test_random_sequence_is_not_repaired(tmp_path):
    p=tmp_path/'random.csv'; out=tmp_path/'out.csv'; rep=tmp_path/'rep.json'
    rows=[[str(i),str((i*17+3)%101),str((i*11+7)%37),str((i*5+2)%19)] for i in range(40)]
    rows[20][1]=''
    write_csv(p,['step','a','b','c'],rows)
    cfg=RepairConfig(discover_sequential_constraints=True, repair_missing_values=True, repair_sequential_missing=True, sequential_min_rows=10)
    result=repair(p,out,rep,cfg)
    assert read_table(out).rows[20][1]==''
    assert result.report['sequential_constraint_discovery']['stable_relations']==0


def test_numeric_projection_refuses_out_of_domain_source(tmp_path):
    p=tmp_path/'numood.csv'; out=tmp_path/'out.csv'; rep=tmp_path/'rep.json'
    rows=[]
    for q in range(1,25):
        price=q+2
        rows.append([str(q),str(price),str(q*price)])
    rows.append(['999','7',''])
    write_csv(p,['qty','price','total'],rows)
    cfg=RepairConfig(discover_numeric_constraints=True, repair_missing_values=True, numeric_missing_min_constraints=1, discovery_min_rows=8)
    repair(p,out,rep,cfg)
    assert read_table(out).rows[-1][2]==''


def test_scope_discovery_separates_total_rows_from_detail_rows(tmp_path):
    p=tmp_path/'aggregate_scope.csv'
    rows=[]
    for kind,k in [('detail',2),('total',5)]:
        for x in range(1,18):
            rows.append([kind,str(x),str(k*x)])
    write_csv(p,['row_type','x','amount'],rows)
    tab=read_table(p)
    reg=discover_scoped_relations(tab, RepairConfig(discover_scoped_relations=True, scope_min_rows=8))
    scopes={(r['scope_column'],r['scope_value']) for r in reg['relations']}
    assert ('row_type','detail') in scopes and ('row_type','total') in scopes


def test_sequential_registry_is_stable_on_clean_running_balance(tmp_path):
    p=tmp_path/'seqclean.csv'; rows=[]; bal=0
    rows.append(['0','0','0','0'])
    for i in range(1,35):
        inc=i+3; outv=i%5; bal += inc-outv
        rows.append([str(i),str(bal),str(inc),str(outv)])
    write_csv(p,['step','balance','in','out'],rows)
    reg=discover_sequential_constraints(read_table(p), RepairConfig(discover_sequential_constraints=True, sequential_min_rows=10))
    assert reg['stable_relations'] >= 1
