from __future__ import annotations

from pathlib import Path

from csv_consistency_repair.engine import RepairConfig, repair
from csv_consistency_repair.maxima50 import (
    FEATURE_IDS, build_bundle_maxima50, build_maxima50_diagnostics, feature_registry,
    relationship_diagnostics, temporal_sampling_diagnostics,
)
from csv_consistency_repair.models import Table


def test_pass013_registry_is_exactly_50_distinct_features():
    reg = feature_registry()
    assert reg['count'] == 50
    assert len(FEATURE_IDS) == len(set(FEATURE_IDS)) == 50
    assert set(map(int, reg['features'])) == set(FEATURE_IDS)


def test_structure_locale_datetime_unit_duplicate_missingness_surfaces():
    t = Table(
        header=[' id ', 'amount', 'date', 'length', 'status', 'status'],
        rows=[
            ['1', '1,234.50', '2026-01-02', '100 cm', 'ok'],
            ['2', '2,345.50', '2026-01-03', '200 cm', 'ok', 'x'],
            [' 2 ', '2,345.50', '2026-01-03', '200 cm', 'OK', 'x'],
            ['3', '<5', '2026-01-04', '300 cm', '', 'x'],
        ],
    )
    d = build_maxima50_diagnostics(t)
    # This short row is ambiguous between two string columns, so the safe layer abstains.
    assert d['input_structure']['row_recovery'][0]['unique_safe_alignment'] is False
    u = build_maxima50_diagnostics(Table(['id','name','value'], [['1','A','10'],['2','B','20'],['3','C','30'],['4','D']]))
    assert u['input_structure']['row_recovery'][0]['unique_safe_alignment'] is True
    assert d['input_structure']['header_repair']['proposals']
    assert any(x['style'] == 'us' for x in d['input_structure']['locale_numeric'])
    assert any(x['roundtrip_safe'] for x in d['input_structure']['datetime_timezone'])
    assert len(d['input_structure']['unit_currency']['recognized_cells']) >= 3
    assert d['duplicates']['near_families']
    states = d['missingness_uncertainty']['states']
    assert states['CENSORED'] == 1 and states['MISSING_KNOWN'] >= 1
    assert d['missingness_uncertainty']['censoring'][0]['interval']['upper'] == 5


def _relation_table() -> Table:
    rows=[]
    for i in range(1,41):
        g='A' if i<=20 else 'B'
        sub='x' if i%2 else 'y'
        a=float(i)
        b=2*a
        c=a+b
        d=3*a + 2*b
        season=float(i%4)
        rows.append([str(i),g,sub,str(a),str(b),str(c),str(d),str(season)])
    return Table(['id','group','sub','a','b','c','d','season'], rows)


def test_relationship_family_composite_conservation_order_fd_mi_keys_scopes_lags_seasonality():
    t=_relation_table()
    r=relationship_diagnostics(t,5)
    assert r['composite_formulas']
    assert r['linear_conservation']
    assert r['order_constraints']
    assert any(x['arity'] >= 2 for x in r['functional_dependencies'])
    assert r['mutual_information']
    assert any(x['columns'] == [0] for x in r['candidate_keys'])
    assert r['conditional_rules']
    assert r['nested_regimes']
    assert r['lag_relations']
    assert r['seasonal_relations']


def test_aggregate_detail_relation():
    rows=[
        ['A','10','30'],['A','20','30'],['B','7','12'],['B','5','12'],
    ]
    t=Table(['group','amount','total'],rows)
    r=relationship_diagnostics(t,5)
    assert len(r['aggregate_checks']) == 2
    assert all(x['consistent'] for x in r['aggregate_checks'])


def test_irregular_sampling_and_multirate():
    rows=[]
    # channel A every second, B every two seconds, interleaved
    for i in range(20):
        rows.append([(f'2026-01-01T00:00:{i:02d}+00:00'),'A',str(i)])
        if i%2==0:
            rows.append([(f'2026-01-01T00:00:{i:02d}+00:00'),'B',str(i)])
    t=Table(['timestamp','channel','value'],rows)
    d=temporal_sampling_diagnostics(t)
    assert d['irregular_sampling']
    assert d['multirate']


def test_low_rank_sparse_state_bayes_interpolation_forward_backward_and_interval():
    rows=[]
    for i in range(1,9):
        a=float(i); b=2*a; c=4*a
        rows.append([str(a),str(b),str(c),str(i)])
    rows[5][2]=''
    rows[3][3]=''
    t=Table(['a','b','c','trend'],rows)
    d=build_maxima50_diagnostics(t)
    low=d['reconstruction']['low_rank_completion']
    assert any(x['row']==5 and x['column']==2 and abs(x['value']-24)<1e-9 for x in low['completions'])
    assert 'applicable' in d['reconstruction']['robust_low_rank_sparse']
    assert 'applicable' in d['reconstruction']['sparse_recovery']
    assert any(x['row']==3 and x['column']==3 for x in d['reconstruction']['bounded_interpolation'])
    assert any(x['row']==3 and x['column']==3 for x in d['reconstruction']['forward_backward_agreement'])
    assert any(x['row']==3 and x['column']==3 for x in d['reconstruction']['interval_reconstruction'])
    # State/Bayes are gated by forward/backward agreement.
    assert any(x['row']==3 and x['column']==3 for x in d['reconstruction']['state_space_reconstruction'])
    assert any(x['row']==3 and x['column']==3 for x in d['reconstruction']['bayesian_filter_reconstruction'])


def test_arbitrary_depth_reachability_and_validation_guards():
    t=_relation_table()
    d=build_maxima50_diagnostics(t)
    assert d['reconstruction']['arbitrary_depth_chains']
    v=d['validation']
    assert v['nested_validation']
    assert v['locked_holdout']
    assert v['negative_controls']
    assert v['distributional_stress']
    assert isinstance(v['multivariate_ood'], list)
    assert v['inverse_sensitivity']


def test_bundle_relationship_join_recovery_schema_version_and_cross_file_reconstruction():
    a=Table(['id','name','schema_version'],[['1','Alice','1'],['2','','1'],['3','Cara','1']])
    b=Table(['id','name','schema_version'],[['1','Alice','2'],['2','Bob','2'],['3','Cara','2']])
    d=build_bundle_maxima50({'left':a,'right':b})
    assert d['automatic_multitable_relationships']
    assert d['join_key_recovery']
    assert any(x['file']=='left' and x['value']=='Bob' for x in d['cross_file_reconstruction'])
    assert len(d['schema_version_registries']) == 2


def test_material_safe_repairs_are_reversible(tmp_path: Path):
    src=tmp_path/'in.csv'; out=tmp_path/'out.csv'; rep=tmp_path/'rep.json'
    # US numeric style is decisive and header canonicalization is reversible.
    src.write_text(' id ,amount\n1,"1,234.50"\n2,"2,345.50"\n3,"3,456.50"\n4,"4,567.50"\n',encoding='utf-8')
    cfg=RepairConfig(maxima50=True,maxima_repair=True,maxima_repair_headers=True,maxima_repair_locale_numbers=True,max_cycles=6)
    result=repair(src,out,rep,cfg)
    assert result.report['maxima50']['after']['enabled']
    text=out.read_text(encoding='utf-8')
    assert text.startswith('id,amount')
    assert '1234.5' in text and '4567.5' in text
    assert result.report['closure']['forward_replay_pass'] is True
    assert result.report['closure']['inverse_roundtrip_pass'] is True


def test_material_rank1_missing_projection_is_multiwitness_and_reversible(tmp_path: Path):
    src=tmp_path/'in.csv'; out=tmp_path/'out.csv'; rep=tmp_path/'rep.json'
    src.write_text('a,b,c\n1,2,4\n2,4,8\n3,6,12\n4,8,\n5,10,20\n',encoding='utf-8')
    cfg=RepairConfig(maxima50=True,maxima_repair=True,maxima_repair_low_rank_missing=True,max_cycles=6)
    result=repair(src,out,rep,cfg)
    assert '4,8,16' in out.read_text(encoding='utf-8')
    assert result.report['closure']['forward_replay_pass'] is True
    assert result.report['closure']['inverse_roundtrip_pass'] is True
