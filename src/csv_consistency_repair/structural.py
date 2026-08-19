from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from itertools import combinations
from typing import Any, Iterable
import hashlib

from .models import AnalysisResult, Candidate, Issue, Table


def _norm_value(value: str | None) -> tuple[str, str]:
    s = '' if value is None else str(value).strip()
    if not s:
        return ('text', '')
    try:
        d = Decimal(s.replace('−', '-'))
        if d == 0:
            return ('num', '0')
        n = format(d.normalize(), 'f')
        if '.' in n:
            n = n.rstrip('0').rstrip('.')
        return ('num', n)
    except (InvalidOperation, ValueError):
        return ('text', s)


def _evidence_ids(issue: Issue) -> list[str]:
    meta = issue.metadata or {}
    ids: list[str] = []
    if meta.get('relation_id'):
        ids.append(str(meta['relation_id']))
    for key in ('relation_ids', 'supporting_relations'):
        raw = meta.get(key)
        if isinstance(raw, (list, tuple, set)):
            ids.extend(str(x) for x in raw)
    if not ids:
        ids.append(issue.code)
    return sorted(set(ids))


def _witness_family(issue: Issue, evidence_id: str) -> str:
    # Analyzer family is deliberately part of the identity. Two reports emitted by
    # the same detector from the same relation are one witness, not repeated proof.
    return f"{issue.analyzer}|{evidence_id}"


def _candidate_id(*parts: Any) -> str:
    return hashlib.sha1(repr(parts).encode('utf-8')).hexdigest()[:16]


def augment_with_cross_analyzer_consensus(
    analysis: AnalysisResult,
    table: Table,
    config: Any,
) -> AnalysisResult:
    """Add a repair candidate only when independent analyzer families agree.

    This does not make a weak single detector stronger. It only fuses already
    reported, cell-specific expected values, requires exact agreement after a
    numeric/text canonicalization, and abstains on any conflicting expectation.
    """
    if not bool(getattr(config, 'structural_consensus', True)):
        return analysis
    min_families = max(2, int(getattr(config, 'structural_consensus_min_families', 2)))

    # A repeated mapping among purely numeric columns is not an independent witness from
    # an algebraic numeric constraint: both can be the same equation viewed in opposite
    # directions. Counting them as two families can turn one corrupted operand into two
    # apparently supported but wrong repairs. Keep such mappings available to their own
    # analyzer, but do not use them as orthogonal evidence in cross-family consensus.
    numeric_columns: set[str] = set()
    for c,name in enumerate(table.header):
        vals=[row[c].strip() for row in table.rows[:512] if c < len(row) and row[c].strip()]
        if len(vals) >= 4:
            ok=0
            for v in vals:
                try: Decimal(v.replace('−','-')); ok+=1
                except (InvalidOperation, ValueError): pass
            if ok / len(vals) >= 0.95:
                numeric_columns.add(name)

    def orthogonal_issue(issue: Issue) -> bool:
        if issue.analyzer != 'relationship_discovery':
            return True
        meta=issue.metadata or {}
        cols=[str(x) for x in (meta.get('determinant') or [])]
        dep=meta.get('dependent')
        if dep is not None: cols.append(str(dep))
        return not (cols and all(x in numeric_columns for x in cols))

    by_cell: dict[tuple[int, int], list[tuple[Issue, str, tuple[str, str]]]] = defaultdict(list)
    for issue in analysis.issues:
        if not orthogonal_issue(issue):
            continue
        if issue.row is None or issue.column is None:
            continue
        expected = (issue.metadata or {}).get('expected')
        if expected is None:
            continue
        for eid in _evidence_ids(issue):
            by_cell[(int(issue.row), int(issue.column))].append(
                (issue, str(expected), _norm_value(str(expected)))
            )

    existing = {
        (c.row, c.column, _norm_value(c.new_value))
        for c in analysis.candidates
        if c.operation == 'set_cell' and c.row is not None and c.column is not None
    }
    new_issues = list(analysis.issues)
    new_candidates = list(analysis.candidates)

    for (row, col), items in sorted(by_cell.items()):
        groups: dict[tuple[str, str], list[tuple[Issue, str]]] = defaultdict(list)
        for issue, expected, norm in items:
            groups[norm].append((issue, expected))
        if len(groups) != 1:
            # Conflicting reconstructions are recorded diagnostically but never repaired.
            values = sorted({expected for _, expected, _ in items})
            if len(values) > 1:
                old = table.rows[row][col] if row < len(table.rows) and col < len(table.rows[row]) else None
                new_issues.append(Issue(
                    analyzer='structural_consensus',
                    code='conflicting_reconstruction_witnesses',
                    message=f"Independent repair paths disagree for this cell: {values}.",
                    severity='warning', row=row, column=col, value=old, repairable=False,
                    metadata={'candidate_values': values, 'unrepaired_reason': 'conflicting_witnesses'},
                ))
            continue

        norm, supporters = next(iter(groups.items()))
        analyzer_families = sorted({issue.analyzer for issue, _ in supporters})
        evidence_ids = sorted({eid for issue, _ in supporters for eid in _evidence_ids(issue)})
        witness_families = sorted({
            _witness_family(issue, eid)
            for issue, _ in supporters
            for eid in _evidence_ids(issue)
        })
        if len(analyzer_families) < min_families or len(evidence_ids) < min_families:
            continue
        expected = supporters[0][1]
        key = (row, col, norm)
        if key in existing:
            continue
        if row >= len(table.rows) or col >= len(table.rows[row]):
            continue
        old = table.rows[row][col]
        confidences = []
        for issue, _ in supporters:
            meta = issue.metadata or {}
            try:
                confidences.append(float(meta.get('confidence', 1.0)))
            except (TypeError, ValueError):
                confidences.append(1.0)
        confidence = min(confidences) if confidences else 1.0
        new_issues.append(Issue(
            analyzer='structural_consensus',
            code='cross_analyzer_consensus',
            message=f"{len(analyzer_families)} independent analyzer families reconstruct the same value {expected!r}.",
            severity='error', row=row, column=col, value=old, repairable=True,
            metadata={
                'expected': expected,
                'analyzer_families': analyzer_families,
                'witness_families': witness_families,
                'evidence_ids': evidence_ids,
                'independent_families': min(len(analyzer_families), len(evidence_ids)),
            },
        ))
        new_candidates.append(Candidate(
            candidate_id=_candidate_id('cross_analyzer_consensus', row, col, old, expected, analyzer_families, witness_families),
            analyzer='structural_consensus', operation='set_cell',
            reason='Repair only when distinct consistency analyzers independently reconstruct the same cell value.',
            row=row, column=col, old_value=old, new_value=expected, cost=1,
            confidence=confidence, reversible=True,
            metadata={
                'rule_type': 'cross_analyzer_consensus',
                'analyzer_families': analyzer_families,
                'witness_families': witness_families,
                'evidence_ids': evidence_ids,
                'independent_families': min(len(analyzer_families), len(evidence_ids)),
            },
        ))
        existing.add(key)

    return AnalysisResult(issues=new_issues, candidates=new_candidates, evidence=dict(analysis.evidence))


def _relation_cells(
    table: Table,
    issue: Issue,
    relation_maps: dict[str, dict[str, Any]],
) -> list[tuple[int, int]]:
    if issue.row is None:
        return []
    r = int(issue.row)
    rel_ids = _evidence_ids(issue)
    cells: set[tuple[int, int]] = set()
    for rid in rel_ids:
        rel = relation_maps.get(rid)
        if not rel:
            continue
        kind = rel.get('type')
        if kind in {'mapping', 'numeric_formula', 'scoped_formula', 'row_segment_formula', 'temporal'}:
            for name in rel.get('columns', []):
                if name in table.header:
                    cells.add((r, table.header.index(name)))
        elif kind == 'running_balance':
            balance = rel.get('balance')
            inflow = rel.get('inflow')
            outflow = rel.get('outflow')
            if balance in table.header:
                bi = table.header.index(balance)
                cells.add((r, bi))
                if r > 0:
                    cells.add((r - 1, bi))
            for name in (inflow, outflow):
                if name and name in table.header:
                    cells.add((r, table.header.index(name)))
    if not cells and issue.column is not None:
        cells.add((r, int(issue.column)))
    return sorted(cells)


def _build_relation_maps(
    relationship_registry: dict[str, Any],
    numeric_registry: dict[str, Any],
    scoped_registry: dict[str, Any],
    sequential_registry: dict[str, Any],
    temporal_registry: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for rel in relationship_registry.get('relationships', []):
        out[str(rel['relation_id'])] = {
            'type': 'mapping',
            'columns': list(rel.get('determinant', [])) + [rel.get('dependent')],
        }
    for rel in numeric_registry.get('relations', []):
        out[str(rel['relation_id'])] = {
            'type': 'numeric_formula',
            'columns': list(rel.get('sources', [])) + [rel.get('target')],
        }
    for rel in scoped_registry.get('relations', []):
        out[str(rel['relation_id'])] = {
            'type': 'scoped_formula',
            'columns': [rel.get('scope_column'), rel.get('source'), rel.get('target')],
        }
    for rel in scoped_registry.get('row_segment_relations', []):
        base = str(rel['relation_id'])
        payload = {'type': 'row_segment_formula', 'columns': [rel.get('source'), rel.get('target')]}
        out[base] = payload
        out[base + 'L'] = payload
        out[base + 'R'] = payload
    for rel in sequential_registry.get('relations', []):
        out[str(rel['relation_id'])] = {
            'type': 'running_balance',
            'balance': rel.get('balance'),
            'inflow': rel.get('inflow'),
            'outflow': rel.get('outflow'),
        }
    for rel in temporal_registry.get('relations', []):
        out[str(rel['relation_id'])] = {
            'type': 'temporal',
            'columns': [rel.get('start'), rel.get('end'), rel.get('duration')],
        }
    return out


def _minimum_hitting_set(edges: list[set[tuple[int, int]]]) -> tuple[list[tuple[int, int]], str]:
    edges = [set(e) for e in edges if e]
    if not edges:
        return [], 'none'
    universe = sorted(set().union(*edges))
    # Exact search is deliberately bounded. Beyond this size, deterministic greedy
    # localization avoids combinatorial blowups while still providing a useful rank.
    if len(universe) <= 24 and len(edges) <= 40:
        max_k = min(6, len(universe))
        for k in range(1, max_k + 1):
            for combo in combinations(universe, k):
                chosen = set(combo)
                if all(chosen & edge for edge in edges):
                    return list(combo), 'exact_minimum_cardinality'
    remaining = list(edges)
    chosen: list[tuple[int, int]] = []
    while remaining:
        counts = Counter(cell for edge in remaining for cell in edge)
        cell, _ = min(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        chosen.append(cell)
        remaining = [edge for edge in remaining if cell not in edge]
    return chosen, 'deterministic_greedy'


def build_structural_diagnostics(
    table: Table,
    analysis: AnalysisResult,
    relationship_registry: dict[str, Any] | None = None,
    numeric_registry: dict[str, Any] | None = None,
    scoped_registry: dict[str, Any] | None = None,
    sequential_registry: dict[str, Any] | None = None,
    temporal_registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    relationship_registry = relationship_registry or {}
    numeric_registry = numeric_registry or {}
    scoped_registry = scoped_registry or {}
    sequential_registry = sequential_registry or {}
    temporal_registry = temporal_registry or {}
    relation_maps = _build_relation_maps(
        relationship_registry, numeric_registry, scoped_registry, sequential_registry, temporal_registry
    )

    syndrome: dict[tuple[int, int], dict[str, Any]] = {}
    constraint_edges: list[set[tuple[int, int]]] = []
    for idx, issue in enumerate(analysis.issues):
        if issue.row is None:
            continue
        cells = _relation_cells(table, issue, relation_maps)
        if not cells:
            continue
        constraint_edges.append(set(cells))
        constraint_id = f"{issue.analyzer}:{issue.code}:{idx}"
        for cell in cells:
            entry = syndrome.setdefault(cell, {
                'row': cell[0], 'column': cell[1],
                'column_name': table.header[cell[1]] if cell[1] < len(table.header) else None,
                'violation_count': 0,
                'severity_weight': 0.0,
                'constraint_ids': [],
                'relation_ids': set(),
                'analyzer_families': set(),
            })
            entry['violation_count'] += 1
            entry['severity_weight'] += {'error': 10.0, 'warning': 3.0, 'info': 1.0}.get(issue.severity, 1.0)
            entry['constraint_ids'].append(constraint_id)
            entry['analyzer_families'].add(issue.analyzer)
            entry['relation_ids'].update(_evidence_ids(issue))

    # Expected-value evidence and witness independence.
    expected_by_cell: dict[tuple[int, int], dict[tuple[str, str], list[tuple[Issue, str]]]] = defaultdict(lambda: defaultdict(list))
    for issue in analysis.issues:
        if issue.row is None or issue.column is None:
            continue
        expected = (issue.metadata or {}).get('expected')
        if expected is None:
            continue
        expected_by_cell[(int(issue.row), int(issue.column))][_norm_value(str(expected))].append((issue, str(expected)))

    cell_reports = []
    for cell, entry in syndrome.items():
        groups = expected_by_cell.get(cell, {})
        analyzer_families = sorted(entry['analyzer_families'])
        relation_ids = sorted(entry['relation_ids'])
        witness_signatures = sorted({
            _witness_family(issue, eid)
            for supporters in groups.values()
            for issue, _ in supporters
            for eid in _evidence_ids(issue)
        })
        expected_values = sorted({value for supporters in groups.values() for _, value in supporters})
        independent_analyzers = sorted({issue.analyzer for supporters in groups.values() for issue, _ in supporters})
        if len(groups) > 1:
            correctability = 'CONFLICTING_RECONSTRUCTIONS'
        elif len(groups) == 1 and len(independent_analyzers) >= 2:
            correctability = 'UNIQUE_MULTI_WITNESS'
        elif len(groups) == 1:
            correctability = 'SINGLE_WITNESS'
        else:
            correctability = 'LOCALIZED_NO_RECONSTRUCTION'
        reliability = min(1.0, len(witness_signatures) / 4.0) if witness_signatures else 0.0
        cell_reports.append({
            'row': entry['row'], 'column': entry['column'], 'column_name': entry['column_name'],
            'violation_count': entry['violation_count'],
            'severity_weight': entry['severity_weight'],
            'constraint_ids': sorted(entry['constraint_ids']),
            'relation_ids': relation_ids,
            'analyzer_families': analyzer_families,
            'independent_witness_count': len(witness_signatures),
            'independent_witnesses': witness_signatures,
            'expected_values': expected_values,
            'correctability': correctability,
            'reliability_score': reliability,
        })

    min_set, solver = _minimum_hitting_set(constraint_edges)
    hit_set = set(min_set)
    for entry in cell_reports:
        entry['in_minimum_explanation'] = (entry['row'], entry['column']) in hit_set

    ranked = sorted(
        cell_reports,
        key=lambda e: (
            -int(e['in_minimum_explanation']),
            -e['severity_weight'],
            -e['violation_count'],
            -e['independent_witness_count'],
            e['row'], e['column'],
        ),
    )
    minimum_explanation = [
        {
            'row': r,
            'column': c,
            'column_name': table.header[c] if c < len(table.header) else None,
        }
        for r, c in min_set
    ]
    uniquely_correctable = sum(1 for e in cell_reports if e['correctability'] == 'UNIQUE_MULTI_WITNESS')
    conflicting = sum(1 for e in cell_reports if e['correctability'] == 'CONFLICTING_RECONSTRUCTIONS')

    # Reliability/redundancy summaries make the graph useful even when no edit is safe.
    by_column: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for entry in cell_reports:
        by_column[int(entry['column'])].append(entry)
    column_reliability = []
    for c, entries in sorted(by_column.items()):
        witness_counts = [int(e['independent_witness_count']) for e in entries]
        column_reliability.append({
            'column': c,
            'column_name': table.header[c] if c < len(table.header) else None,
            'syndrome_cells': len(entries),
            'mean_independent_witnesses': (sum(witness_counts) / len(witness_counts) if witness_counts else 0.0),
            'max_independent_witnesses': max(witness_counts) if witness_counts else 0,
            'multi_witness_fraction': (sum(x >= 2 for x in witness_counts) / len(witness_counts) if witness_counts else 0.0),
        })
    table_reliability = {
        'syndrome_cells': len(cell_reports),
        'mean_independent_witnesses': (sum(e['independent_witness_count'] for e in cell_reports) / len(cell_reports) if cell_reports else 0.0),
        'multi_witness_cells': sum(e['independent_witness_count'] >= 2 for e in cell_reports),
    }

    # Common-cause / burst diagnostics. These are conservative labels only: they do not
    # themselves authorize an edit.
    issue_rows_by_column: dict[int, list[int]] = defaultdict(list)
    scale_ratios_by_column: dict[int, list[tuple[int, float]]] = defaultdict(list)
    for issue in analysis.issues:
        if issue.row is None or issue.column is None:
            continue
        r, c = int(issue.row), int(issue.column)
        issue_rows_by_column[c].append(r)
        meta = issue.metadata or {}
        exp = meta.get('expected')
        try:
            obs = Decimal(str(issue.value)) if issue.value not in (None, '') else None
            expected = Decimal(str(exp)) if exp not in (None, '') else None
            if obs is not None and expected not in (None, Decimal(0)):
                ratio = float(obs / expected)
                if ratio == ratio and abs(ratio) < 1e12:
                    scale_ratios_by_column[c].append((r, ratio))
        except (InvalidOperation, ValueError, TypeError, ZeroDivisionError):
            pass

    common_causes = []
    for c, rows in sorted(issue_rows_by_column.items()):
        uniq = sorted(set(rows))
        runs = []
        start = prev = None
        for r in uniq:
            if start is None:
                start = prev = r
            elif r == prev + 1:
                prev = r
            else:
                if prev - start + 1 >= 3:
                    runs.append([start, prev])
                start = prev = r
        if start is not None and prev - start + 1 >= 3:
            runs.append([start, prev])
        density = len(uniq) / max(1, len(table.rows))
        if runs or density >= 0.10:
            common_causes.append({
                'kind': 'burst_or_column_wide',
                'column': c,
                'column_name': table.header[c] if c < len(table.header) else None,
                'affected_rows': len(uniq),
                'row_density': density,
                'contiguous_runs': runs,
            })
        ratios = [x[1] for x in scale_ratios_by_column.get(c, [])]
        if len(ratios) >= 4:
            # Robustly find a dominant multiplicative fault even when a few unrelated
            # violations share the same column. The cluster must still have >=4 witnesses.
            best_cluster: list[float] = []
            for center0 in ratios:
                cluster = [x for x in ratios if abs(x-center0) / max(abs(center0), 1e-12) <= 0.02]
                if len(cluster) > len(best_cluster):
                    best_cluster = cluster
            if len(best_cluster) >= 4:
                center = sorted(best_cluster)[len(best_cluster)//2]
                if center not in (0.0, 1.0):
                    rel_spread = max(abs(x-center) for x in best_cluster) / max(abs(center), 1e-12)
                    common_causes.append({
                        'kind': 'shared_scale_shift',
                        'column': c,
                        'column_name': table.header[c] if c < len(table.header) else None,
                        'estimated_scale_ratio': center,
                        'support': len(best_cluster),
                        'total_numeric_violations': len(ratios),
                        'relative_spread': rel_spread,
                    })

    # Preserve ambiguity explicitly and say what additional evidence would help.
    candidate_sets = []
    next_evidence = []
    for entry in ranked:
        if entry['correctability'] == 'CONFLICTING_RECONSTRUCTIONS':
            candidate_sets.append({
                'row': entry['row'], 'column': entry['column'], 'column_name': entry['column_name'],
                'candidate_values': entry['expected_values'],
                'status': 'multiple_legal_candidates',
            })
            next_evidence.append({
                'row': entry['row'], 'column': entry['column'], 'column_name': entry['column_name'],
                'reason': 'conflicting_witnesses',
                'suggestion': 'Add or validate an independent relation, reference file, or source column that distinguishes the candidate values.',
                'existing_relations': entry['relation_ids'],
            })
        elif entry['correctability'] == 'LOCALIZED_NO_RECONSTRUCTION':
            next_evidence.append({
                'row': entry['row'], 'column': entry['column'], 'column_name': entry['column_name'],
                'reason': 'localized_without_reconstruction',
                'suggestion': 'Provide one independent constraint or redundant source that directly reconstructs this cell.',
                'existing_relations': entry['relation_ids'],
            })
        elif entry['correctability'] == 'SINGLE_WITNESS':
            next_evidence.append({
                'row': entry['row'], 'column': entry['column'], 'column_name': entry['column_name'],
                'reason': 'single_witness_only',
                'suggestion': 'Provide a second independent witness before automatic repair.',
                'existing_relations': entry['relation_ids'],
            })

    return {
        'enabled': True,
        'constraint_syndrome_cells': len(cell_reports),
        'violated_constraint_count': len(constraint_edges),
        'minimum_explanation_solver': solver,
        'minimum_explanation_size': len(minimum_explanation),
        'minimum_explanation_cells': minimum_explanation,
        'uniquely_correctable_cells': uniquely_correctable,
        'conflicting_reconstruction_cells': conflicting,
        'fault_isolation_ranking': ranked,
        'reliability': {'table': table_reliability, 'columns': column_reliability},
        'common_cause_candidates': common_causes,
        'candidate_sets': candidate_sets,
        'suggested_next_evidence': next_evidence,
        'features': {
            'constraint_syndrome': True,
            'minimum_hitting_set_localization': True,
            'witness_independence': True,
            'correctability_classification': True,
            'fault_isolation_ranking': True,
            'reliability_redundancy_scoring': True,
            'common_cause_burst_detection': True,
            'candidate_set_preservation': True,
            'suggested_next_evidence': True,
        },
    }
