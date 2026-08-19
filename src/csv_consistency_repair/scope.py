from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from itertools import combinations
from typing import Any
import hashlib

from .models import AnalysisResult, Candidate, Issue, Table


def _id(*parts: Any) -> str:
    return hashlib.sha1(repr(parts).encode('utf-8')).hexdigest()[:16]


def _dec(value: str) -> Decimal | None:
    try:
        s = value.strip().replace('−', '-')
        return Decimal(s) if s else None
    except (InvalidOperation, ValueError):
        return None


def _fmt(value: Decimal) -> str:
    if value == 0:
        return '0'
    s = format(value.normalize(), 'f')
    return s.rstrip('0').rstrip('.') if '.' in s else s


def _close(a: Decimal, b: Decimal, abs_tol: Decimal, rel_tol: Decimal) -> bool:
    return abs(a - b) <= abs_tol + rel_tol * max(abs(b), Decimal('1'))


def _median(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    n = len(ordered)
    if n % 2:
        return ordered[n // 2]
    return (ordered[n // 2 - 1] + ordered[n // 2]) / Decimal(2)


def _partitions(indexes: list[int]) -> list[list[int]]:
    if not indexes:
        return []
    even = indexes[::2]
    odd = indexes[1::2]
    half = len(indexes) // 2
    return [even, odd, indexes[:half], indexes[half:]]


def _numeric_columns(table: Table, min_rows: int, max_columns: int) -> list[int]:
    out: list[int] = []
    for c in range(min(len(table.header), max_columns)):
        vals = [row[c] for row in table.rows if c < len(row) and row[c].strip()]
        if len(vals) < min_rows:
            continue
        ratio = sum(_dec(v) is not None for v in vals) / len(vals)
        if ratio >= 0.9:
            out.append(c)
    return out


def _categorical_scope_columns(table: Table, min_rows: int, max_scopes: int, max_columns: int) -> list[int]:
    candidates: list[int] = []
    n = max(1, len(table.rows))
    for c in range(min(len(table.header), max_columns)):
        vals = [row[c].strip() for row in table.rows if c < len(row) and row[c].strip()]
        if len(vals) < min_rows:
            continue
        unique = set(vals)
        if 2 <= len(unique) <= max_scopes and len(unique) / n <= 0.25:
            candidates.append(c)
    return candidates


@dataclass(frozen=True)
class AffineFit:
    slope: Decimal
    intercept: Decimal
    eligible_rows: int
    confidence: float
    exact_confidence: float
    source_min: Decimal
    source_max: Decimal
    target_min: Decimal
    target_max: Decimal
    violations: tuple[tuple[int, str, str], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            'slope': _fmt(self.slope),
            'intercept': _fmt(self.intercept),
            'eligible_rows': self.eligible_rows,
            'confidence': self.confidence,
            'exact_confidence': self.exact_confidence,
            'source_range': [_fmt(self.source_min), _fmt(self.source_max)],
            'target_range': [_fmt(self.target_min), _fmt(self.target_max)],
            'violations': [
                {'row': row, 'observed': observed, 'expected': expected}
                for row, observed, expected in self.violations
            ],
        }


def _robust_affine_fit(
    table: Table,
    source: int,
    target: int,
    indexes: list[int],
    abs_tol: Decimal,
    rel_tol: Decimal,
) -> AffineFit | None:
    points: list[tuple[int, Decimal, Decimal, str]] = []
    for r in indexes:
        row = table.rows[r]
        if max(source, target) >= len(row):
            continue
        x = _dec(row[source])
        y = _dec(row[target])
        if x is None or y is None:
            continue
        points.append((r, x, y, row[target]))
    if len(points) < 4 or len({p[1] for p in points}) < 3:
        return None

    # Deterministic robust slope estimate from a bounded pair sample.
    sample = points if len(points) <= 40 else points[::max(1, len(points)//40)][:40]
    slopes: list[Decimal] = []
    for (_, x1, y1, _), (_, x2, y2, _) in combinations(sample, 2):
        if x2 == x1:
            continue
        slopes.append((y2 - y1) / (x2 - x1))
    if not slopes:
        return None
    slope = _median(slopes)
    intercept = _median([y - slope * x for _, x, y, _ in points])

    matched = exact = 0
    violations: list[tuple[int, str, str]] = []
    for r, x, y, observed in points:
        expected = slope * x + intercept
        if _close(y, expected, abs_tol, rel_tol):
            matched += 1
            if y == expected:
                exact += 1
        else:
            violations.append((r, observed, _fmt(expected)))
    xs = [p[1] for p in points]
    ys = [p[2] for p in points]
    return AffineFit(
        slope=slope,
        intercept=intercept,
        eligible_rows=len(points),
        confidence=matched / len(points),
        exact_confidence=exact / len(points),
        source_min=min(xs), source_max=max(xs), target_min=min(ys), target_max=max(ys),
        violations=tuple(violations),
    )


def _fit_stability(
    table: Table,
    source: int,
    target: int,
    indexes: list[int],
    config,
) -> dict[str, Any] | None:
    min_rows = max(8, int(getattr(config, 'scope_min_rows', 12)))
    threshold = max(0.9, float(getattr(config, 'scope_confidence', 0.98)))
    abs_tol = Decimal(str(getattr(config, 'numeric_abs_tolerance', 0.000001)))
    rel_tol = Decimal(str(getattr(config, 'numeric_rel_tolerance', 0.000001)))
    base = _robust_affine_fit(table, source, target, indexes, abs_tol, rel_tol)
    if base is None or base.eligible_rows < min_rows:
        return None
    partition_fits = []
    partition_pass = True
    applicable = 0
    for part in _partitions(indexes):
        fit = _robust_affine_fit(table, source, target, part, abs_tol, rel_tol)
        if fit is None or fit.eligible_rows < max(4, min_rows // 3):
            continue
        applicable += 1
        slope_close = _close(fit.slope, base.slope, abs_tol * 10, rel_tol * 10)
        intercept_close = _close(fit.intercept, base.intercept, abs_tol * 10, rel_tol * 10)
        passed = fit.confidence >= threshold and slope_close and intercept_close
        partition_pass = partition_pass and passed
        partition_fits.append({
            'eligible_rows': fit.eligible_rows,
            'confidence': fit.confidence,
            'slope': _fmt(fit.slope),
            'intercept': _fmt(fit.intercept),
            'pass': passed,
        })
    tighter = _robust_affine_fit(table, source, target, indexes, abs_tol / 2, rel_tol / 2)
    tight_pass = bool(tighter and tighter.confidence >= threshold)
    stable = bool(base.confidence >= threshold and applicable >= 2 and partition_pass and tight_pass)
    return {
        'pass': stable,
        'base': base.to_dict(),
        'partition_checks': partition_fits,
        'tight_tolerance_confidence': tighter.confidence if tighter else 0.0,
    }


def discover_scoped_relations(table: Table, config) -> dict[str, Any]:
    if not getattr(config, 'discover_scoped_relations', False):
        return {'enabled': False, 'scope_columns': [], 'relations': [], 'stable_relations': 0, 'row_segment_relations': []}

    min_rows = max(8, int(getattr(config, 'scope_min_rows', 12)))
    max_scopes = max(2, int(getattr(config, 'scope_max_groups', 12)))
    max_columns = max(3, int(getattr(config, 'numeric_max_columns', 12)))
    numeric = _numeric_columns(table, min_rows, max_columns)
    scope_cols = [c for c in _categorical_scope_columns(table, min_rows, max_scopes, max_columns) if c not in numeric]
    relations: list[dict[str, Any]] = []

    for scope_col in scope_cols:
        groups: dict[str, list[int]] = defaultdict(list)
        for r, row in enumerate(table.rows):
            if scope_col < len(row) and row[scope_col].strip():
                groups[row[scope_col].strip()].append(r)
        eligible_groups = {k: v for k, v in groups.items() if len(v) >= min_rows}
        if len(eligible_groups) < 2:
            continue
        for source in numeric:
            for target in numeric:
                if source == target:
                    continue
                group_fits = []
                for label, indexes in sorted(eligible_groups.items()):
                    contract = _fit_stability(table, source, target, indexes, config)
                    if contract and contract['pass']:
                        group_fits.append((label, indexes, contract))
                if len(group_fits) < 2:
                    continue
                signatures = {(g[2]['base']['slope'], g[2]['base']['intercept']) for g in group_fits}
                if len(signatures) < 2:
                    # Same formula in every group is already a global relation, not a scope-specific rule.
                    continue
                for label, indexes, contract in group_fits:
                    relation_id = _id('scoped_affine', table.header[scope_col], label, table.header[source], table.header[target], contract['base']['slope'], contract['base']['intercept'])
                    relations.append({
                        'relation_id': relation_id,
                        'kind': 'scoped_affine',
                        'scope_column': table.header[scope_col],
                        'scope_index': scope_col,
                        'scope_value': label,
                        'source': table.header[source],
                        'source_index': source,
                        'target': table.header[target],
                        'target_index': target,
                        'row_count': len(indexes),
                        'stability': contract,
                    })

    # Boundary/change-point discovery on row order. This is conservative: only report/repair when
    # two large adjacent segments have independently exact/stable affine laws with different coefficients.
    row_segment_relations: list[dict[str, Any]] = []
    n = len(table.rows)
    if n >= 2 * min_rows:
        candidate_splits = sorted(set([n // 3, n // 2, (2 * n) // 3]))
        for source in numeric:
            for target in numeric:
                if source == target:
                    continue
                best = None
                for split in candidate_splits:
                    if split < min_rows or n - split < min_rows:
                        continue
                    left = _fit_stability(table, source, target, list(range(0, split)), config)
                    right = _fit_stability(table, source, target, list(range(split, n)), config)
                    if not left or not right or not left['pass'] or not right['pass']:
                        continue
                    ls = (left['base']['slope'], left['base']['intercept'])
                    rs = (right['base']['slope'], right['base']['intercept'])
                    if ls == rs:
                        continue
                    score = min(float(left['base']['confidence']), float(right['base']['confidence']))
                    item = {
                        'relation_id': _id('row_segment_affine', split, table.header[source], table.header[target], ls, rs),
                        'kind': 'row_segment_affine',
                        'split_row_index': split,
                        'left_rows': [0, split - 1],
                        'right_rows': [split, n - 1],
                        'source': table.header[source],
                        'source_index': source,
                        'target': table.header[target],
                        'target_index': target,
                        'left_stability': left,
                        'right_stability': right,
                        'confidence': score,
                    }
                    if best is None or score > best['confidence']:
                        best = item
                if best:
                    row_segment_relations.append(best)

    return {
        'enabled': True,
        'scope_columns': [table.header[c] for c in scope_cols],
        'relations': relations,
        'stable_relations': len(relations),
        'row_segment_relations': row_segment_relations,
        'row_segment_relation_count': len(row_segment_relations),
    }


def _in_range(value: Decimal, lo: str, hi: str) -> bool:
    dlo, dhi = _dec(lo), _dec(hi)
    return dlo is not None and dhi is not None and dlo <= value <= dhi


class ScopedRelationAnalyzer:
    name = 'scoped_relation_discovery'

    def analyze(self, table: Table, config) -> AnalysisResult:
        out = AnalysisResult()
        registry = discover_scoped_relations(table, config)
        out.evidence['scoped'] = registry
        if not registry.get('enabled'):
            return out
        repair_missing = bool(getattr(config, 'repair_missing_values', False) and getattr(config, 'repair_scoped_missing', False))
        repair_existing = bool(getattr(config, 'repair_scoped_values', False))
        votes: dict[tuple[int, int, str], list[tuple[dict[str, Any], str]]] = defaultdict(list)

        def process_relation(rel: dict[str, Any], indexes: list[int], contract: dict[str, Any]) -> None:
            si, ti = int(rel['source_index']), int(rel['target_index'])
            slope = _dec(contract['base']['slope'])
            intercept = _dec(contract['base']['intercept'])
            if slope is None or intercept is None:
                return
            source_range = contract['base']['source_range']
            for r in indexes:
                row = table.rows[r]
                if max(si, ti) >= len(row):
                    continue
                x = _dec(row[si])
                if x is None or not _in_range(x, source_range[0], source_range[1]):
                    continue
                expected = _fmt(slope * x + intercept)
                observed = row[ti].strip()
                if observed == '':
                    out.issues.append(Issue(
                        self.name, 'missing_value_reconstructible_in_scope',
                        f"Missing {table.header[ti]} can be computed from a stable formula valid in this row scope.",
                        'error', row=r, column=ti, value='', repairable=repair_missing,
                        metadata={'relation_id': rel['relation_id'], 'expected': expected, 'scope': rel.get('scope_value', rel.get('kind')), 'source_range': source_range},
                    ))
                    if repair_missing:
                        out.candidates.append(Candidate(
                            candidate_id=_id('scoped_missing', rel['relation_id'], r, ti, expected),
                            analyzer=self.name, operation='set_cell',
                            reason='Compute a missing cell only inside the learned scope and source range of a stable formula.',
                            row=r, column=ti, old_value='', new_value=expected, cost=1, confidence=float(contract['base']['confidence']), reversible=True,
                            metadata={'rule_type': 'scoped_formula_missing_projection', 'relation_id': rel['relation_id'], 'scope': rel.get('scope_value'), 'source_range': source_range},
                        ))
                else:
                    y = _dec(observed)
                    exp = _dec(expected)
                    if y is None or exp is None:
                        continue
                    abs_tol = Decimal(str(getattr(config, 'numeric_abs_tolerance', 0.000001)))
                    rel_tol = Decimal(str(getattr(config, 'numeric_rel_tolerance', 0.000001)))
                    if not _close(y, exp, abs_tol, rel_tol):
                        votes[(r, ti, observed)].append((rel, expected))

        for rel in registry.get('relations', []):
            scope_i = int(rel['scope_index'])
            indexes = [r for r, row in enumerate(table.rows) if scope_i < len(row) and row[scope_i].strip() == rel['scope_value']]
            process_relation(rel, indexes, rel['stability'])

        for rel in registry.get('row_segment_relations', []):
            split = int(rel['split_row_index'])
            left_rel = rel | {'relation_id': rel['relation_id'] + 'L'}
            right_rel = rel | {'relation_id': rel['relation_id'] + 'R'}
            process_relation(left_rel, list(range(0, split)), rel['left_stability'])
            process_relation(right_rel, list(range(split, len(table.rows))), rel['right_stability'])

        # Existing nonblank repairs require independent scoped formulas to agree; a single scoped
        # formula is diagnostic only. This keeps regime discovery useful without turning it into guesswork.
        for (r, c, old), items in votes.items():
            by_value: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for rel, expected in items:
                by_value[expected].append(rel)
            expected, supporters = max(by_value.items(), key=lambda kv: len(kv[1]))
            independent = {(rel.get('source'), rel.get('kind'), rel.get('scope_value'), rel.get('split_row_index')) for rel in supporters}
            repairable = repair_existing and len(independent) >= 2
            out.issues.append(Issue(
                self.name, 'scoped_formula_violation',
                f"Value conflicts with {len(independent)} independently scoped stable formulas; expected {expected!r}.",
                'warning', row=r, column=c, value=old, repairable=repairable,
                metadata={'expected': expected, 'supporting_relations': [x['relation_id'] for x in supporters], 'independent_constraints': len(independent)},
            ))
            if repairable:
                out.candidates.append(Candidate(
                    candidate_id=_id('scoped_consensus', r, c, old, expected, sorted(x['relation_id'] for x in supporters)),
                    analyzer=self.name, operation='set_cell',
                    reason='Repair an existing value only when multiple independently scoped stable formulas agree.',
                    row=r, column=c, old_value=old, new_value=expected, cost=1,
                    confidence=min(float(x.get('stability', x.get('left_stability', {})).get('base', {}).get('confidence', 1.0)) for x in supporters), reversible=True,
                    metadata={'rule_type': 'scoped_formula_consensus', 'independent_constraints': len(independent), 'relation_ids': [x['relation_id'] for x in supporters]},
                ))
        return out
