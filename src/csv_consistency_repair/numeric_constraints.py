from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation, getcontext
from itertools import combinations
from typing import Any
import hashlib

from .models import AnalysisResult, Candidate, Issue, Table

getcontext().prec = 28


def _id(*parts: Any) -> str:
    return hashlib.sha1(repr(parts).encode('utf-8')).hexdigest()[:16]


def _dec(s: str) -> Decimal | None:
    try:
        t = s.strip().replace('−', '-')
        if not t:
            return None
        return Decimal(t)
    except (InvalidOperation, ValueError):
        return None


def _fmt(x: Decimal) -> str:
    if x == 0:
        return '0'
    s = format(x.normalize(), 'f')
    return s.rstrip('0').rstrip('.') if '.' in s else s


def _close(a: Decimal, b: Decimal, abs_tol: Decimal, rel_tol: Decimal) -> bool:
    lim = abs_tol + rel_tol * max(abs(b), Decimal('1'))
    return abs(a - b) <= lim


def _partitions(n: int):
    h = n // 2
    return [list(range(0, n, 2)), list(range(1, n, 2)), list(range(0, h)), list(range(h, n))]


def _eval(kind: str, vals: list[Decimal]) -> Decimal | None:
    try:
        if kind == 'sum':
            return vals[0] + vals[1]
        if kind == 'difference':
            return vals[0] - vals[1]
        if kind == 'product':
            return vals[0] * vals[1]
        if kind == 'ratio':
            return None if vals[1] == 0 else vals[0] / vals[1]
        if kind == 'sum3':
            return vals[0] + vals[1] + vals[2]
        if kind == 'sum_minus':
            return vals[0] + vals[1] - vals[2]
        if kind == 'product_plus':
            return vals[0] * vals[1] + vals[2]
        if kind == 'product_minus':
            return vals[0] * vals[1] - vals[2]
    except (InvalidOperation, ZeroDivisionError):
        return None
    return None


def _solve_one(kind: str, values: list[Decimal | None], target: Decimal | None) -> dict[int | str, Decimal]:
    """Solve a discovered equation for the single missing variable.

    Returns keys 0..n-1 for source positions or 'target'. Only equations with a
    unique elementary inverse are supported.
    """
    out: dict[int | str, Decimal] = {}
    missing_sources = [i for i, v in enumerate(values) if v is None]
    if target is None and not missing_sources and all(v is not None for v in values):
        got = _eval(kind, [v for v in values if v is not None])
        if got is not None:
            out['target'] = got
        return out
    if target is None or len(missing_sources) != 1:
        return out
    i = missing_sources[0]
    v = values
    try:
        if kind == 'sum':
            out[i] = target - v[1 - i]  # type: ignore[operator]
        elif kind == 'difference':
            if i == 0:
                out[0] = target + v[1]  # type: ignore[operator]
            else:
                out[1] = v[0] - target  # type: ignore[operator]
        elif kind == 'product':
            other = v[1 - i]
            if other not in (None, Decimal(0)):
                out[i] = target / other
        elif kind == 'ratio':
            if i == 0 and v[1] is not None:
                out[0] = target * v[1]
            elif i == 1 and target != 0 and v[0] is not None:
                out[1] = v[0] / target
        elif kind == 'sum3':
            known = sum((x for x in v if x is not None), Decimal(0))
            out[i] = target - known
        elif kind == 'sum_minus':
            a, b, c = v
            if i == 0 and b is not None and c is not None:
                out[0] = target - b + c
            elif i == 1 and a is not None and c is not None:
                out[1] = target - a + c
            elif i == 2 and a is not None and b is not None:
                out[2] = a + b - target
        elif kind == 'product_plus':
            a, b, c = v
            if i == 0 and b not in (None, Decimal(0)) and c is not None:
                out[0] = (target - c) / b
            elif i == 1 and a not in (None, Decimal(0)) and c is not None:
                out[1] = (target - c) / a
            elif i == 2 and a is not None and b is not None:
                out[2] = target - a * b
        elif kind == 'product_minus':
            a, b, c = v
            if i == 0 and b not in (None, Decimal(0)) and c is not None:
                out[0] = (target + c) / b
            elif i == 1 and a not in (None, Decimal(0)) and c is not None:
                out[1] = (target + c) / a
            elif i == 2 and a is not None and b is not None:
                out[2] = a * b - target
    except (InvalidOperation, ZeroDivisionError, TypeError):
        return {}
    return out


def _build_numeric_cache(table: Table, width: int) -> dict[int, list[Decimal | None]]:
    """Parse each numeric-looking cell at most once per discovery pass.

    Numeric discovery evaluates many candidate equations over the same columns.  The old
    implementation reparsed Decimal strings for every candidate relation, which dominated
    runtime on 1k-100k row tables.  The cache preserves Decimal semantics while removing
    that repeated parsing work.
    """
    return {
        c: [_dec(row[c]) if c < len(row) else None for row in table.rows]
        for c in range(width)
    }


def _relation_stats(table: Table, target: int, sources: tuple[int, ...], kind: str, rows, abs_tol: Decimal, rel_tol: Decimal, cache: dict[int, list[Decimal | None]] | None = None, min_confidence: float | None = None):
    eligible = matched = 0
    violations = []
    exact = 0
    max_col = max((target,) + sources)
    total_rows = len(rows) if hasattr(rows, '__len__') else None
    for pos, r in enumerate(rows):
        row = table.rows[r]
        if max_col >= len(row):
            continue
        if cache is None:
            tv = _dec(row[target])
            vals = [_dec(row[c]) for c in sources]
        else:
            tv = cache[target][r]
            vals = [cache[c][r] for c in sources]
        if tv is None or any(v is None for v in vals):
            continue
        exp = _eval(kind, [v for v in vals if v is not None])
        if exp is None:
            continue
        eligible += 1
        if _close(tv, exp, abs_tol, rel_tol):
            matched += 1
            if tv == exp:
                exact += 1
        else:
            violations.append((r, row[target], _fmt(exp)))
        # Safe early rejection for obviously false relations.  Even if every remaining
        # row were eligible and matched, the final confidence could not recover above
        # min_confidence.  This is only an optimization; accepted relations are still
        # scanned completely.
        if min_confidence is not None and total_rows is not None and pos >= 31:
            remaining = total_rows - pos - 1
            upper = (matched + remaining) / (eligible + remaining) if eligible + remaining else 0.0
            if upper < min_confidence:
                return eligible, (matched / eligible if eligible else 0.0), (exact / eligible if eligible else 0.0), violations
    return eligible, (matched / eligible if eligible else 0.0), (exact / eligible if eligible else 0.0), violations


def _candidate_relations(numeric: list[int], target: int, max_terms: int):
    sources = [c for c in numeric if c != target]
    for a, b in combinations(sources, 2):
        yield (a, b), 'sum'
        yield (a, b), 'product'
        yield (a, b), 'difference'
        yield (b, a), 'difference'
        yield (a, b), 'ratio'
        yield (b, a), 'ratio'
    if max_terms < 3:
        return
    for combo in combinations(sources, 3):
        a, b, c = combo
        yield (a, b, c), 'sum3'
        # each source can play the signed/additive sidecar role
        for x, y, z in ((a, b, c), (a, c, b), (b, c, a)):
            yield (x, y, z), 'sum_minus'
            yield (x, y, z), 'product_plus'
            yield (x, y, z), 'product_minus'


def _relation_ranges(table: Table, target: int, sources: tuple[int, ...], cache: dict[int, list[Decimal | None]] | None = None) -> dict[str, Any]:
    complete: list[tuple[Decimal, list[Decimal]]] = []
    max_col = max((target,) + sources)
    for r, row in enumerate(table.rows):
        if max_col >= len(row):
            continue
        if cache is None:
            tv = _dec(row[target])
            sv = [_dec(row[c]) for c in sources]
        else:
            tv = cache[target][r]
            sv = [cache[c][r] for c in sources]
        if tv is None or any(v is None for v in sv):
            continue
        complete.append((tv, [v for v in sv if v is not None]))
    source_ranges = []
    for pos in range(len(sources)):
        vals = [sv[pos] for _, sv in complete]
        source_ranges.append([_fmt(min(vals)), _fmt(max(vals))] if vals else None)
    targets = [tv for tv, _ in complete]
    target_range = [_fmt(min(targets)), _fmt(max(targets))] if targets else None
    return {'source_ranges': source_ranges, 'target_range': target_range}


def _value_in_range(value: Decimal | None, bounds) -> bool:
    if value is None or bounds is None:
        return True
    lo, hi = _dec(str(bounds[0])), _dec(str(bounds[1]))
    return lo is not None and hi is not None and lo <= value <= hi


def _row_within_relation_scope(row: list[str], rel: dict[str, Any], missing_column: int | None = None) -> bool:
    sources = tuple(int(x) for x in rel['source_indexes'])
    for pos, c in enumerate(sources):
        if c == missing_column:
            continue
        if c >= len(row) or not _value_in_range(_dec(row[c]), rel.get('source_ranges', [None] * len(sources))[pos]):
            return False
    target = int(rel['target_index'])
    if target != missing_column and target < len(row) and not _value_in_range(_dec(row[target]), rel.get('target_range')):
        return False
    return True


def discover_numeric_constraints(table: Table, config) -> dict[str, Any]:
    if not getattr(config, 'discover_numeric_constraints', False):
        return {'enabled': False, 'relations': [], 'stable_relations': 0, 'unstable_relations': 0, 'consensus_cells': 0}
    width = min(len(table.header), int(getattr(config, 'numeric_max_columns', 12)))
    min_rows = max(8, int(getattr(config, 'discovery_min_rows', 12)))
    threshold = float(getattr(config, 'discovery_confidence', 0.95))
    stress_tol = float(getattr(config, 'discovery_stress_tolerance', 0.05))
    abs_tol = Decimal(str(getattr(config, 'numeric_abs_tolerance', 0.000001)))
    rel_tol = Decimal(str(getattr(config, 'numeric_rel_tolerance', 0.000001)))
    max_terms = max(2, min(3, int(getattr(config, 'numeric_max_formula_terms', 2))))
    cache = _build_numeric_cache(table, width)
    numeric = []
    for c in range(width):
        present = [r for r, row in enumerate(table.rows) if c < len(row) and row[c].strip()]
        if len(present) >= min_rows and sum(cache[c][r] is not None for r in present) / len(present) >= 0.9:
            numeric.append(c)

    relations = []
    seen = set()
    # Use a bounded deterministic sample as a cheap prefilter for 3-term expressions.
    if len(table.rows) <= 400:
        sample_rows = list(range(len(table.rows)))
    else:
        step = max(1, len(table.rows) // 400)
        sample_rows = list(range(0, len(table.rows), step))[:400]

    # Majority-consensus floor for sparse corruption.  Repair authority still requires
    # multiple independent equations to reconstruct the same cell, so discovery can stay
    # permissive enough to survive substantial outlier rates without turning one noisy
    # equation into an edit.
    robust_floor = max(0.70, threshold - max(stress_tol, 0.25))
    prefilter_floor = max(0.60, robust_floor - 0.10)

    for target in numeric:
        for sources, kind in _candidate_relations(numeric, target, max_terms):
            if kind in ('sum', 'product', 'sum3'):
                canonical_sources = tuple(sorted(sources))
            else:
                canonical_sources = tuple(sources)
            key = (target, canonical_sources, kind)
            if key in seen:
                continue
            seen.add(key)
            se, sc, _, _ = _relation_stats(table, target, sources, kind, sample_rows, abs_tol, rel_tol, cache)
            if se < max(6, min_rows // 2) or sc < prefilter_floor:
                continue
            eligible, conf, exact_conf, viol = _relation_stats(table, target, sources, kind, range(len(table.rows)), abs_tol, rel_tol, cache, prefilter_floor)
            if eligible < min_rows or conf < prefilter_floor:
                continue
            pconfs = []
            for rows in _partitions(len(table.rows)):
                e, c, _, _ = _relation_stats(table, target, sources, kind, rows, abs_tol, rel_tol, cache, max(0.60, robust_floor - 0.10))
                if e >= max(4, min_rows // 3):
                    pconfs.append(c)
            # Robust sparse-corruption gate.  A relation is allowed to survive a small
            # number of outliers when the same equation remains coherent across independent
            # row partitions and under a tighter numeric tolerance.  This removes the old
            # discontinuity where 5% corruption passed and 6% failed outright, without
            # granting repair authority to a single relation (repairs still require
            # independent reconstruction witnesses below).
            partition_floor = max(0.60, robust_floor - 0.10)
            partition_pass = len(pconfs) >= 2 and sum(c >= partition_floor for c in pconfs) >= max(2, len(pconfs) - 1)
            tight_e, tight_conf, _, _ = _relation_stats(table, target, sources, kind, range(len(table.rows)), abs_tol / 2, rel_tol / 2, cache, robust_floor)
            tight_pass = tight_e >= min_rows and tight_conf >= robust_floor
            sparse_violation_pass = (eligible - int(round(conf * eligible))) <= max(1, int(0.30 * eligible))
            stable = eligible >= min_rows and conf >= robust_floor and partition_pass and tight_pass and sparse_violation_pass
            ranges = _relation_ranges(table, target, sources, cache) if stable else {'source_ranges': [None] * len(sources), 'target_range': None}
            relations.append({
                'relation_id': _id('numeric', table.header[target], tuple(table.header[c] for c in sources), kind),
                'kind': 'numeric_equation',
                'target': table.header[target],
                'sources': [table.header[c] for c in sources],
                'source_indexes': list(sources),
                'target_index': target,
                'operation': kind,
                'eligible_rows': eligible,
                'confidence': conf,
                'exact_confidence': exact_conf,
                'partition_confidences': pconfs,
                'tight_tolerance_confidence': tight_conf,
                'stable': stable,
                'violations': [{'row': r, 'observed': old, 'expected': new} for r, old, new in viol],
                **ranges,
            })
    stable = sum(1 for r in relations if r['stable'])
    return {
        'enabled': True,
        'relations': relations,
        'stable_relations': stable,
        'unstable_relations': len(relations) - stable,
        'numeric_columns': [table.header[c] for c in numeric],
        'max_formula_terms': max_terms,
    }


class NumericConstraintAnalyzer:
    name = 'numeric_constraint_consensus'

    def analyze(self, table: Table, config) -> AnalysisResult:
        out = AnalysisResult()
        reg = discover_numeric_constraints(table, config)
        out.evidence['numeric'] = reg
        if not reg['enabled']:
            return out
        votes = defaultdict(list)
        missing_votes = defaultdict(list)
        abs_tol = Decimal(str(getattr(config, 'numeric_abs_tolerance', 0.000001)))
        rel_tol = Decimal(str(getattr(config, 'numeric_rel_tolerance', 0.000001)))

        for rel in reg['relations']:
            if not rel['stable']:
                continue
            target_i = int(rel['target_index'])
            sources = tuple(int(x) for x in rel['source_indexes'])
            # Existing target violations.
            for v in rel['violations']:
                r = int(v['row'])
                old = str(v['observed'])
                exp = str(v['expected'])
                out.issues.append(Issue(
                    self.name,
                    'numeric_constraint_violation',
                    f"{rel['target']}={old!r} violates stable {rel['operation']} relation from {rel['sources']}.",
                    'warning', row=r, column=target_i, value=old, repairable=False,
                    metadata={'relation_id': rel['relation_id'], 'expected': exp, 'confidence': rel['confidence']},
                ))
                if _row_within_relation_scope(table.rows[r], rel, missing_column=target_i):
                    votes[(r, target_i, old)].append((rel, exp, 'target'))

            # Structural projection: if exactly one member of a stable equation is missing,
            # solve for that member. This allows ordinary spreadsheet-style formula recovery.
            for r, row in enumerate(table.rows):
                if max((target_i,) + sources) >= len(row):
                    continue
                target = _dec(row[target_i])
                vals = [_dec(row[c]) for c in sources]
                missing_count = int(target is None and row[target_i].strip() == '') + sum(
                    1 for c, v in zip(sources, vals) if v is None and row[c].strip() == ''
                )
                # Do not treat non-numeric text as a missing value.
                if missing_count != 1:
                    continue
                solved = _solve_one(rel['operation'], vals, target)
                if not solved:
                    continue
                for position, value in solved.items():
                    if position == 'target':
                        c = target_i
                    else:
                        c = sources[int(position)]
                    if row[c].strip() != '':
                        continue
                    if not _row_within_relation_scope(row, rel, missing_column=c):
                        continue
                    missing_votes[(r, c, '')].append((rel, _fmt(value), position))

        need = max(2, int(getattr(config, 'numeric_min_independent_constraints', 2)))
        repair = bool(getattr(config, 'repair_numeric_constraints', False))
        missing_repair = bool(getattr(config, 'repair_missing_values', False))
        missing_need = max(1, int(getattr(config, 'numeric_missing_min_constraints', 1)))

        def cluster_items(items):
            clusters = []
            for rel, exp_s, role in items:
                exp = _dec(exp_s)
                if exp is None:
                    continue
                found = None
                for cluster in clusters:
                    if _close(exp, cluster[0], abs_tol, rel_tol):
                        found = cluster
                        break
                if found is None:
                    found = [exp, []]
                    clusters.append(found)
                found[1].append((rel, exp_s, role))
            return clusters

        for (r, c, old), items in votes.items():
            clusters = cluster_items(items)
            if not clusters:
                continue
            best = max(clusters, key=lambda z: len(z[1]))
            independent = {(tuple(x[0]['sources']), x[0]['operation']) for x in best[1]}
            if len(independent) < need:
                continue
            expected = _fmt(best[0])
            conf = min(float(x[0]['confidence']) for x in best[1])
            out.issues.append(Issue(
                self.name, 'redundant_constraint_consensus',
                f"{len(independent)} independent stable constraints reconstruct the same value {expected!r}.",
                'error', row=r, column=c, value=old, repairable=repair,
                metadata={'expected': expected, 'independent_constraints': len(independent), 'relation_ids': [x[0]['relation_id'] for x in best[1]]},
            ))
            if repair:
                out.candidates.append(Candidate(
                    candidate_id=_id('consensus', r, c, old, expected, sorted(x[0]['relation_id'] for x in best[1])),
                    analyzer=self.name, operation='set_cell',
                    reason='Repair only when multiple independently discovered stable equations reconstruct the same cell value.',
                    row=r, column=c, old_value=old, new_value=expected, cost=1, confidence=conf, reversible=True,
                    metadata={'rule_type': 'redundant_numeric_constraint_consensus', 'independent_constraints': len(independent), 'relation_ids': [x[0]['relation_id'] for x in best[1]]},
                ))

        for (r, c, old), items in missing_votes.items():
            clusters = cluster_items(items)
            if not clusters:
                continue
            best = max(clusters, key=lambda z: len(z[1]))
            independent = {(tuple(x[0]['sources']), x[0]['operation'], x[2]) for x in best[1]}
            # One relation may fill a blank only when the relation is exact across all eligible rows.
            strict_single = len(independent) == 1 and all(float(x[0].get('exact_confidence', 0.0)) >= 1.0 for x in best[1])
            if len(independent) < missing_need or (len(independent) == 1 and not strict_single):
                continue
            expected = _fmt(best[0])
            conf = min(float(x[0]['confidence']) for x in best[1])
            repairable = missing_repair
            out.issues.append(Issue(
                self.name, 'missing_numeric_value_reconstructible',
                f"Stable numeric constraints reconstruct missing cell as {expected!r}.",
                'error', row=r, column=c, value='', repairable=repairable,
                metadata={
                    'expected': expected,
                    'independent_constraints': len(independent),
                    'strict_single_relation': strict_single,
                    'relation_ids': [x[0]['relation_id'] for x in best[1]],
                },
            ))
            if repairable:
                out.candidates.append(Candidate(
                    candidate_id=_id('missing_numeric_projection', r, c, expected, sorted(x[0]['relation_id'] for x in best[1])),
                    analyzer=self.name, operation='set_cell',
                    reason='Compute a missing numeric cell from stable spreadsheet-style relations, with exact single-relation or multi-relation support.',
                    row=r, column=c, old_value='', new_value=expected, cost=1, confidence=conf, reversible=True,
                    metadata={
                        'rule_type': 'missing_numeric_projection',
                        'projection': True,
                        'independent_constraints': len(independent),
                        'strict_single_relation': strict_single,
                        'relation_ids': [x[0]['relation_id'] for x in best[1]],
                    },
                ))
        return out
