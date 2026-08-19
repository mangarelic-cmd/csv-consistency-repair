from __future__ import annotations

from collections import defaultdict
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


def _numeric_columns(table: Table, min_rows: int, max_columns: int) -> list[int]:
    out = []
    for c in range(min(len(table.header), max_columns)):
        vals = [row[c] for row in table.rows if c < len(row) and row[c].strip()]
        if len(vals) < min_rows:
            continue
        if sum(_dec(v) is not None for v in vals) / len(vals) >= 0.9:
            out.append(c)
    return out


def _sequence_stats(table: Table, balance: int, inflow: int, outflow: int | None, rows: list[int], abs_tol: Decimal, rel_tol: Decimal):
    eligible = matched = exact = 0
    violations = []
    for r in rows:
        if r <= 0 or r >= len(table.rows):
            continue
        cur = table.rows[r]
        prev = table.rows[r - 1]
        maxc = max(balance, inflow, outflow if outflow is not None else 0)
        if maxc >= len(cur) or balance >= len(prev):
            continue
        b_prev = _dec(prev[balance])
        b_cur = _dec(cur[balance])
        inc = _dec(cur[inflow])
        out = _dec(cur[outflow]) if outflow is not None else Decimal(0)
        if b_prev is None or b_cur is None or inc is None or out is None:
            continue
        expected = b_prev + inc - out
        eligible += 1
        if _close(b_cur, expected, abs_tol, rel_tol):
            matched += 1
            if b_cur == expected:
                exact += 1
        else:
            violations.append({'row': r, 'observed': cur[balance], 'expected': _fmt(expected)})
    return eligible, (matched / eligible if eligible else 0.0), (exact / eligible if eligible else 0.0), violations


def discover_sequential_constraints(table: Table, config) -> dict[str, Any]:
    if not getattr(config, 'discover_sequential_constraints', False):
        return {'enabled': False, 'relations': [], 'stable_relations': 0}
    min_rows = max(10, int(getattr(config, 'sequential_min_rows', 16)))
    threshold = max(0.95, float(getattr(config, 'sequential_confidence', 0.99)))
    abs_tol = Decimal(str(getattr(config, 'numeric_abs_tolerance', 0.000001)))
    rel_tol = Decimal(str(getattr(config, 'numeric_rel_tolerance', 0.000001)))
    numeric = _numeric_columns(table, min_rows, int(getattr(config, 'numeric_max_columns', 12)))
    relations = []
    n = len(table.rows)
    rows = list(range(1, n))
    for balance in numeric:
        for inflow in numeric:
            if inflow == balance:
                continue
            outflow_choices: list[int | None] = [None] + [c for c in numeric if c not in (balance, inflow)]
            for outflow in outflow_choices:
                e, conf, exact, viol = _sequence_stats(table, balance, inflow, outflow, rows, abs_tol, rel_tol)
                allowed_defects = max(2, int(e * 0.05))
                robust_base_pass = e >= min_rows and (conf >= threshold or len(viol) <= allowed_defects)
                if not robust_base_pass:
                    continue
                parts = [rows[::2], rows[1::2], rows[:len(rows)//2], rows[len(rows)//2:]]
                pconfs = []
                partition_passes = []
                for part in parts:
                    pe, pc, _, pv = _sequence_stats(table, balance, inflow, outflow, part, abs_tol, rel_tol)
                    if pe >= max(4, min_rows // 3):
                        pconfs.append(pc)
                        partition_passes.append(pc >= threshold or len(pv) <= max(2, int(pe * 0.05)))
                stable = len(pconfs) >= 2 and all(partition_passes)
                if not stable:
                    continue
                relations.append({
                    'relation_id': _id('sequence_balance', table.header[balance], table.header[inflow], table.header[outflow] if outflow is not None else None),
                    'kind': 'running_balance',
                    'balance': table.header[balance],
                    'balance_index': balance,
                    'inflow': table.header[inflow],
                    'inflow_index': inflow,
                    'outflow': table.header[outflow] if outflow is not None else None,
                    'outflow_index': outflow,
                    'eligible_rows': e,
                    'confidence': conf,
                    'exact_confidence': exact,
                    'partition_confidences': pconfs,
                    'stable': True,
                    'robust_base_pass': robust_base_pass,
                    'allowed_defects': allowed_defects,
                    'violations': viol,
                })
    # Remove duplicate two-column relations when a three-column relation explains the same balance/inflow more specifically.
    triples = {(r['balance_index'], r['inflow_index']) for r in relations if r['outflow_index'] is not None}
    filtered = [r for r in relations if r['outflow_index'] is not None or (r['balance_index'], r['inflow_index']) not in triples]
    return {'enabled': True, 'relations': filtered, 'stable_relations': len(filtered), 'numeric_columns': [table.header[c] for c in numeric]}


class SequentialConstraintAnalyzer:
    name = 'sequential_consistency'

    def analyze(self, table: Table, config) -> AnalysisResult:
        out = AnalysisResult()
        registry = discover_sequential_constraints(table, config)
        out.evidence['sequential'] = registry
        if not registry.get('enabled'):
            return out
        abs_tol = Decimal(str(getattr(config, 'numeric_abs_tolerance', 0.000001)))
        rel_tol = Decimal(str(getattr(config, 'numeric_rel_tolerance', 0.000001)))
        repair_missing = bool(getattr(config, 'repair_missing_values', False) and getattr(config, 'repair_sequential_missing', False))
        repair_values = bool(getattr(config, 'repair_sequential_values', False))
        balance_votes: dict[tuple[int, int, str], list[tuple[dict[str, Any], str, str]]] = defaultdict(list)

        for rel in registry['relations']:
            bi = int(rel['balance_index'])
            ii = int(rel['inflow_index'])
            oi = rel['outflow_index']
            oi = int(oi) if oi is not None else None
            exact_relation = float(rel['exact_confidence']) >= 1.0 and all(float(x) >= 1.0 for x in rel['partition_confidences'])
            for r in range(1, len(table.rows)):
                cur, prev = table.rows[r], table.rows[r - 1]
                maxc = max(bi, ii, oi if oi is not None else 0)
                if maxc >= len(cur) or bi >= len(prev):
                    continue
                b_prev, b_cur = _dec(prev[bi]), _dec(cur[bi])
                inc = _dec(cur[ii])
                outv = _dec(cur[oi]) if oi is not None else Decimal(0)
                raw = {'prev_balance': prev[bi], 'balance': cur[bi], 'inflow': cur[ii], 'outflow': cur[oi] if oi is not None else '0'}
                missing_fields = [name for name, val in [('prev_balance', b_prev), ('balance', b_cur), ('inflow', inc), ('outflow', outv)] if val is None and (name != 'outflow' or oi is not None)]
                if len(missing_fields) == 1 and exact_relation:
                    field = missing_fields[0]
                    new: Decimal | None = None
                    column = None
                    row_index = r
                    if field == 'balance' and b_prev is not None and inc is not None and outv is not None:
                        new, column = b_prev + inc - outv, bi
                    elif field == 'prev_balance' and b_cur is not None and inc is not None and outv is not None:
                        new, column, row_index = b_cur - inc + outv, bi, r - 1
                    elif field == 'inflow' and b_prev is not None and b_cur is not None and outv is not None:
                        new, column = b_cur - b_prev + outv, ii
                    elif field == 'outflow' and oi is not None and b_prev is not None and b_cur is not None and inc is not None:
                        new, column = b_prev + inc - b_cur, oi
                    if new is not None and column is not None and table.rows[row_index][column].strip() == '':
                        expected = _fmt(new)
                        out.issues.append(Issue(
                            self.name, 'missing_sequential_value_reconstructible',
                            'A missing value can be reconstructed from an exact stable running-balance relation.',
                            'error', row=row_index, column=column, value='', repairable=repair_missing,
                            metadata={'relation_id': rel['relation_id'], 'expected': expected, 'field': field},
                        ))
                        if repair_missing:
                            out.candidates.append(Candidate(
                                candidate_id=_id('sequence_missing', rel['relation_id'], row_index, column, expected),
                                analyzer=self.name, operation='set_cell',
                                reason='Compute one missing sequential value from an exact stable running-balance relation.',
                                row=row_index, column=column, old_value='', new_value=expected, cost=1, confidence=1.0, reversible=True,
                                metadata={'rule_type': 'sequential_missing_projection', 'relation_id': rel['relation_id'], 'field': field},
                            ))
                    continue

                if None in (b_prev, b_cur, inc, outv):
                    continue
                expected = b_prev + inc - outv  # type: ignore[operator]
                if not _close(b_cur, expected, abs_tol, rel_tol):  # type: ignore[arg-type]
                    balance_votes[(r, bi, cur[bi])].append((rel, _fmt(expected), 'forward'))

            # Backward check gives an independent reconstruction of balance[r] from row r+1.
            for r in range(0, len(table.rows) - 1):
                cur, nxt = table.rows[r], table.rows[r + 1]
                maxc = max(bi, ii, oi if oi is not None else 0)
                if bi >= len(cur) or maxc >= len(nxt):
                    continue
                b_cur = _dec(cur[bi])
                b_next = _dec(nxt[bi])
                inc_next = _dec(nxt[ii])
                out_next = _dec(nxt[oi]) if oi is not None else Decimal(0)
                if b_next is None or inc_next is None or out_next is None:
                    continue
                expected = b_next - inc_next + out_next
                if b_cur is None and cur[bi].strip() == '':
                    balance_votes[(r, bi, '')].append((rel, _fmt(expected), 'backward'))
                elif b_cur is not None and not _close(b_cur, expected, abs_tol, rel_tol):
                    balance_votes[(r, bi, cur[bi])].append((rel, _fmt(expected), 'backward'))

        for (r, c, old), items in balance_votes.items():
            groups: dict[str, list[tuple[dict[str, Any], str]]] = defaultdict(list)
            for rel, expected, direction in items:
                groups[expected].append((rel, direction))
            expected, supporters = max(groups.items(), key=lambda kv: len(kv[1]))
            directions = {direction for _, direction in supporters}
            relation_ids = {rel['relation_id'] for rel, _ in supporters}
            # Strongest sequential repair: forward and backward independently agree on the same balance.
            two_sided = directions == {'forward', 'backward'}
            repairable = (repair_missing if old == '' else repair_values) and two_sided
            out.issues.append(Issue(
                self.name, 'two_sided_sequential_reconstruction',
                f"Forward and backward sequence checks reconstruct the same value {expected!r}." if two_sided else 'Sequence inconsistency detected but two-sided reconstruction is unavailable.',
                'error' if two_sided else 'warning', row=r, column=c, value=old, repairable=repairable,
                metadata={'expected': expected, 'two_sided': two_sided, 'directions': sorted(directions), 'relation_ids': sorted(relation_ids)},
            ))
            if repairable:
                out.candidates.append(Candidate(
                    candidate_id=_id('sequence_two_sided', r, c, old, expected, sorted(relation_ids)),
                    analyzer=self.name, operation='set_cell',
                    reason='Repair a sequence value only when forward and backward running-balance checks independently agree.',
                    row=r, column=c, old_value=old, new_value=expected, cost=1, confidence=1.0, reversible=True,
                    metadata={'rule_type': 'two_sided_sequential_reconstruction', 'relation_ids': sorted(relation_ids), 'two_sided': True},
                ))
        return out
