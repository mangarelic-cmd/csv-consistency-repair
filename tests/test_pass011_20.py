from __future__ import annotations

from pathlib import Path

from csv_consistency_repair.advanced_diagnostics import build_advanced_diagnostics
from csv_consistency_repair.engine import RepairConfig, repair
from csv_consistency_repair.models import AnalysisResult, Candidate, Issue, Table
from csv_consistency_repair.structural import build_structural_diagnostics


class TwoCellAnalyzer:
    name = 'two_cell_check'

    def analyze(self, table, config):
        out = AnalysisResult()
        targets = [(0, 0, 'ok1'), (0, 1, 'ok2')]
        for r, c, expected in targets:
            old = table.rows[r][c]
            if old != expected:
                out.issues.append(Issue(
                    self.name, f'bad_{c}', 'wrong', 'error', r, c, old, True,
                    {'relation_id': f'r{c}', 'expected': expected},
                ))
                out.candidates.append(Candidate(
                    candidate_id=f'fix{c}', analyzer=self.name, operation='set_cell',
                    reason='test', row=r, column=c, old_value=old, new_value=expected,
                    confidence=1.0, reversible=True,
                ))
        return out


def test_global_minimum_edit_plan_and_counterfactual_ledger(tmp_path: Path):
    src = tmp_path / 'in.csv'
    out = tmp_path / 'out.csv'
    rep = tmp_path / 'rep.json'
    src.write_text('a,b\nbad1,bad2\n', encoding='utf-8')
    result = repair(src, out, rep, RepairConfig(max_cycles=4), analyzers=(TwoCellAnalyzer(),))
    assert result.final_score == 0
    planning = result.report['global_repair_planning']
    assert planning['features']['global_minimum_edit_plan'] is True
    assert planning['features']['counterfactual_candidate_testing'] is True
    assert planning['cycles'][0]['selected_size'] == 2
    assert len(planning['cycles'][0]['counterfactual_candidates']) == 2
    assert all(e['decision'] == 'committed_global_minimum_edit_plan' for e in result.report['committed_edits'])


def test_structural_reliability_common_cause_candidate_sets_and_evidence():
    rows = [[str(i), '100'] for i in range(12)]
    table = Table(header=['id', 'value'], rows=rows)
    issues = []
    # Four contiguous violations with the same scale ratio -> burst + scale-shift candidate.
    for r in range(3, 7):
        issues.append(Issue('formula', 'bad', 'bad', 'warning', r, 1, '100', False,
                            {'relation_id': f'r{r}', 'expected': '10'}))
    # Ambiguous cell with two independent legal reconstructions.
    issues.extend([
        Issue('left', 'amb1', 'a', 'warning', 8, 1, '100', False, {'relation_id': 'la', 'expected': '9'}),
        Issue('right', 'amb2', 'b', 'warning', 8, 1, '100', False, {'relation_id': 'rb', 'expected': '11'}),
    ])
    diag = build_structural_diagnostics(table, AnalysisResult(issues=issues))
    assert diag['features']['reliability_redundancy_scoring'] is True
    assert diag['reliability']['table']['syndrome_cells'] >= 5
    kinds = {x['kind'] for x in diag['common_cause_candidates']}
    assert 'burst_or_column_wide' in kinds
    assert 'shared_scale_shift' in kinds
    assert diag['candidate_sets'][0]['status'] == 'multiple_legal_candidates'
    assert any(x['reason'] == 'conflicting_witnesses' for x in diag['suggested_next_evidence'])


def _advanced_table() -> Table:
    rows = []
    # Regime shift, recurring signal, saturation, and an autocorrelated formula residual.
    residual = 0.0
    for i in range(80):
        a = float(i + 1)
        b = float((i % 7) + 2)
        residual = 0.82 * residual + (0.02 if i % 2 == 0 else -0.01)
        c = a + b + residual
        shifted = float(i if i < 40 else i + 100)
        recurring = 0.0 if (i // 20) % 2 == 0 else 20.0
        capped = min(float(i), 50.0)
        rows.append([f'{a:.8f}', f'{b:.8f}', f'{c:.8f}', f'{shifted:.8f}', f'{recurring:.8f}', f'{capped:.8f}'])
    return Table(header=['a', 'b', 'c', 'shifted', 'recurring', 'capped'], rows=rows)


def test_advanced_regime_and_statistical_diagnostics_cover_14_features():
    table = _advanced_table()
    numeric = {'relations': [{
        'relation_id': 'sum_rel', 'operation': 'sum', 'target': 'c', 'target_index': 2,
        'sources': ['a', 'b'], 'source_indexes': [0, 1], 'violations': [], 'stable': True,
    }]}
    cfg = RepairConfig(discovery_min_rows=12, numeric_abs_tolerance=1e-6, numeric_rel_tolerance=1e-6)
    diag = build_advanced_diagnostics(table, numeric, {}, cfg)
    features = diag['features']
    expected = {
        'cusum_change_point', 'sequential_evidence_probe', 'adaptive_window_forgetting',
        'recurring_regime_recognition', 'hysteresis_diagnostics', 'multistability_preservation',
        'saturation_dead_zone_detection', 'calibration_normalization_drift', 'epoch_aware_revalidation',
        'residual_whiteness', 'residual_autocorrelation', 'bootstrap_relation_stability',
        'kfold_relation_validation', 'complexity_penalty_ranking',
    }
    assert expected == {k for k, v in features.items() if v}
    assert any(x['change_point_flag'] for x in diag['change_points'])
    assert any(x.get('drift_flag') for x in diag['adaptive_windows'])
    assert any(x.get('recurring_regime_flag') for x in diag['recurring_regimes'])
    assert any(x.get('upper_saturation_flag') for x in diag['saturation'])
    rel = diag['relation_diagnostics'][0]
    assert rel['bootstrap_stability']['applicable'] is True
    assert rel['kfold_validation']['applicable'] is True
    assert rel['complexity_penalty']['applicable'] is True
    assert len(rel['sequential_evidence_probe']['prefixes']) == 4
    assert rel['lag1_autocorrelation'] is not None
    assert len(diag['complexity_ranked_relations']) == 1
