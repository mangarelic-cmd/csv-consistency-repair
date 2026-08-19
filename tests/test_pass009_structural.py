from pathlib import Path

from csv_consistency_repair.engine import RepairConfig, repair
from csv_consistency_repair.models import AnalysisResult, Issue, Table
from csv_consistency_repair.structural import build_structural_diagnostics


class ExpectedFromLeft:
    name = 'left_check'
    def analyze(self, table, config):
        out = AnalysisResult()
        for r, row in enumerate(table.rows):
            if len(row) >= 3 and row[0] == '4' and row[1] == '12' and row[2] != '48':
                out.issues.append(Issue(
                    self.name, 'left_formula', 'left formula disagrees', 'warning',
                    row=r, column=2, value=row[2], repairable=False,
                    metadata={'relation_id': 'left_formula', 'expected': '48', 'confidence': 1.0},
                ))
        return out


class ExpectedFromRight:
    name = 'right_check'
    def analyze(self, table, config):
        out = AnalysisResult()
        for r, row in enumerate(table.rows):
            if len(row) >= 5 and row[3] == '7.2' and row[4] == '55.2' and row[2] != '48':
                out.issues.append(Issue(
                    self.name, 'right_formula', 'right formula disagrees', 'warning',
                    row=r, column=2, value=row[2], repairable=False,
                    metadata={'relation_id': 'right_formula', 'expected': '48.0', 'confidence': 1.0},
                ))
        return out


def test_cross_analyzer_consensus_repairs_when_two_independent_checks_agree(tmp_path: Path):
    src = tmp_path / 'in.csv'
    out = tmp_path / 'out.csv'
    report = tmp_path / 'report.json'
    src.write_text('qty,price,subtotal,tax,total\n4,12,77,7.2,55.2\n', encoding='utf-8')
    result = repair(
        src, out, report,
        RepairConfig(max_cycles=4, stable_cycles_required=2, structural_consensus=True),
        analyzers=(ExpectedFromLeft(), ExpectedFromRight()),
    )
    assert out.read_text(encoding='utf-8').splitlines()[1].split(',')[2] in {'48', '48.0'}
    assert result.committed_edits == 1
    assert result.report['closure']['forward_replay_pass'] is True
    assert result.report['closure']['inverse_roundtrip_pass'] is True


def test_constraint_syndrome_localizes_shared_cell_by_exact_minimum_hitting_set():
    table = Table(header=['A', 'B', 'C'], rows=[['1', '9', '3']])
    analysis = AnalysisResult(issues=[
        Issue('x', 'r1_bad', 'r1', 'warning', row=0, column=0, value='1', metadata={'relation_id': 'r1'}),
        Issue('y', 'r2_bad', 'r2', 'warning', row=0, column=2, value='3', metadata={'relation_id': 'r2'}),
    ])
    numeric = {'relations': [
        {'relation_id': 'r1', 'sources': ['A'], 'target': 'B'},
        {'relation_id': 'r2', 'sources': ['C'], 'target': 'B'},
    ]}
    diag = build_structural_diagnostics(table, analysis, numeric_registry=numeric)
    assert diag['minimum_explanation_solver'] == 'exact_minimum_cardinality'
    assert diag['minimum_explanation_size'] == 1
    assert diag['minimum_explanation_cells'][0]['column_name'] == 'B'


def test_witness_independence_does_not_double_count_same_detector_relation():
    table = Table(header=['x'], rows=[['bad']])
    analysis = AnalysisResult(issues=[
        Issue('same_detector', 'bad1', 'bad', 'warning', row=0, column=0, value='bad', metadata={'relation_id': 'same', 'expected': 'ok'}),
        Issue('same_detector', 'bad2', 'bad again', 'warning', row=0, column=0, value='bad', metadata={'relation_id': 'same', 'expected': 'ok'}),
    ])
    diag = build_structural_diagnostics(table, analysis)
    cell = diag['fault_isolation_ranking'][0]
    assert cell['independent_witness_count'] == 1
    assert cell['correctability'] == 'SINGLE_WITNESS'


def test_correctability_marks_conflicting_reconstructions_without_inventing_winner():
    table = Table(header=['x'], rows=[['bad']])
    analysis = AnalysisResult(issues=[
        Issue('a', 'a_bad', 'a', 'warning', row=0, column=0, value='bad', metadata={'relation_id': 'ra', 'expected': '10'}),
        Issue('b', 'b_bad', 'b', 'warning', row=0, column=0, value='bad', metadata={'relation_id': 'rb', 'expected': '11'}),
    ])
    diag = build_structural_diagnostics(table, analysis)
    cell = diag['fault_isolation_ranking'][0]
    assert cell['correctability'] == 'CONFLICTING_RECONSTRUCTIONS'
    assert diag['conflicting_reconstruction_cells'] == 1


def test_fault_isolation_ranking_prioritizes_cells_explaining_more_constraints():
    table = Table(header=['A', 'B', 'C', 'D'], rows=[['1', '9', '3', '4']])
    analysis = AnalysisResult(issues=[
        Issue('x', 'i1', '1', 'error', row=0, column=0, value='1', metadata={'relation_id': 'r1'}),
        Issue('x', 'i2', '2', 'warning', row=0, column=2, value='3', metadata={'relation_id': 'r2'}),
        Issue('x', 'i3', '3', 'warning', row=0, column=3, value='4', metadata={'relation_id': 'r3'}),
    ])
    numeric = {'relations': [
        {'relation_id': 'r1', 'sources': ['A'], 'target': 'B'},
        {'relation_id': 'r2', 'sources': ['C'], 'target': 'B'},
        {'relation_id': 'r3', 'sources': ['D'], 'target': 'B'},
    ]}
    diag = build_structural_diagnostics(table, analysis, numeric_registry=numeric)
    assert diag['fault_isolation_ranking'][0]['column_name'] == 'B'
    assert diag['fault_isolation_ranking'][0]['violation_count'] == 3
    assert diag['fault_isolation_ranking'][0]['in_minimum_explanation'] is True
