from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from itertools import permutations
from typing import Any
import hashlib

from .models import AnalysisResult, Candidate, Issue, Table


def _id(*parts: Any) -> str:
    return hashlib.sha1(repr(parts).encode('utf-8')).hexdigest()[:16]


def _parse_dt(value: str) -> datetime | None:
    s = value.strip()
    if not s:
        return None
    try:
        if s.endswith('Z'):
            s = s[:-1] + '+00:00'
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _parse_num(value: str) -> Decimal | None:
    try:
        s = value.strip()
        return Decimal(s) if s else None
    except (InvalidOperation, ValueError):
        return None


def _format_dt(dt: datetime, samples: list[str]) -> str:
    # Preserve the simplest dominant ISO shape already used by the column.
    has_time = sum(1 for s in samples if 'T' in s or ' ' in s) >= max(1, len(samples) // 2)
    has_t = sum(1 for s in samples if 'T' in s) >= sum(1 for s in samples if ' ' in s)
    if not has_time:
        return dt.date().isoformat()
    sep = 'T' if has_t else ' '
    if dt.microsecond:
        return dt.isoformat(sep=sep)
    return dt.replace(microsecond=0).isoformat(sep=sep)


def _fmt_num(x: Decimal) -> str:
    if x == 0:
        return '0'
    s = format(x.normalize(), 'f')
    return s.rstrip('0').rstrip('.') if '.' in s else s


def _stats(table: Table, start: int, end: int, duration: int, unit_seconds: Decimal, rows) -> tuple[int, float, float]:
    eligible = matched = exact = 0
    for r in rows:
        row = table.rows[r]
        if max(start, end, duration) >= len(row):
            continue
        a = _parse_dt(row[start])
        b = _parse_dt(row[end])
        d = _parse_num(row[duration])
        if a is None or b is None or d is None:
            continue
        delta = Decimal(str((b - a).total_seconds()))
        expected = d * unit_seconds
        eligible += 1
        tolerance = max(Decimal('0.000001'), abs(expected) * Decimal('0.000001'))
        if abs(delta - expected) <= tolerance:
            matched += 1
            if delta == expected:
                exact += 1
    return eligible, (matched / eligible if eligible else 0.0), (exact / eligible if eligible else 0.0)


def _partitions(n: int):
    h = n // 2
    return [range(0, n, 2), range(1, n, 2), range(0, h), range(h, n)]


def discover_temporal_constraints(table: Table, config) -> dict[str, Any]:
    if not getattr(config, 'discover_temporal_constraints', False):
        return {'enabled': False, 'relations': [], 'stable_relations': 0}
    min_rows = max(8, int(getattr(config, 'discovery_min_rows', 12)))
    threshold = max(0.95, float(getattr(config, 'discovery_confidence', 0.95)))
    width = min(len(table.header), int(getattr(config, 'temporal_max_columns', 12)))
    temporal = []
    numeric = []
    for c in range(width):
        nonempty = [row[c] for row in table.rows if c < len(row) and row[c].strip()]
        if len(nonempty) < min_rows:
            continue
        dt_ratio = sum(_parse_dt(v) is not None for v in nonempty) / len(nonempty)
        num_ratio = sum(_parse_num(v) is not None for v in nonempty) / len(nonempty)
        if dt_ratio >= 0.9:
            temporal.append(c)
        elif num_ratio >= 0.9:
            numeric.append(c)

    units = {
        'seconds': Decimal('1'),
        'minutes': Decimal('60'),
        'hours': Decimal('3600'),
        'days': Decimal('86400'),
    }
    relations = []
    seen = set()
    for start, end in permutations(temporal, 2):
        for duration in numeric:
            for unit, unit_seconds in units.items():
                key = (start, end, duration, unit)
                if key in seen:
                    continue
                seen.add(key)
                eligible, conf, exact_conf = _stats(table, start, end, duration, unit_seconds, range(len(table.rows)))
                if eligible < min_rows or conf < threshold:
                    continue
                pconfs = []
                for rows in _partitions(len(table.rows)):
                    e, c, _ = _stats(table, start, end, duration, unit_seconds, rows)
                    if e >= max(4, min_rows // 3):
                        pconfs.append(c)
                stable = len(pconfs) >= 2 and all(c >= threshold for c in pconfs)
                relations.append({
                    'relation_id': _id('temporal', table.header[start], table.header[end], table.header[duration], unit),
                    'kind': 'elapsed_time',
                    'start': table.header[start],
                    'end': table.header[end],
                    'duration': table.header[duration],
                    'unit': unit,
                    'indexes': {'start': start, 'end': end, 'duration': duration},
                    'eligible_rows': eligible,
                    'confidence': conf,
                    'exact_confidence': exact_conf,
                    'partition_confidences': pconfs,
                    'stable': stable,
                })
    return {
        'enabled': True,
        'relations': relations,
        'stable_relations': sum(1 for r in relations if r['stable']),
        'temporal_columns': [table.header[c] for c in temporal],
        'numeric_duration_candidates': [table.header[c] for c in numeric],
    }


class TemporalConstraintAnalyzer:
    name = 'temporal_consistency'

    def analyze(self, table: Table, config) -> AnalysisResult:
        out = AnalysisResult()
        reg = discover_temporal_constraints(table, config)
        out.evidence['temporal'] = reg
        if not reg['enabled']:
            return out
        repair = bool(getattr(config, 'repair_missing_values', False) and getattr(config, 'repair_temporal_missing', False))
        for rel in reg['relations']:
            if not rel['stable'] or float(rel['exact_confidence']) < 1.0 or not all(float(x) >= 1.0 for x in rel['partition_confidences']):
                continue
            si = int(rel['indexes']['start'])
            ei = int(rel['indexes']['end'])
            di = int(rel['indexes']['duration'])
            unit_seconds = {'seconds': Decimal('1'), 'minutes': Decimal('60'), 'hours': Decimal('3600'), 'days': Decimal('86400')}[rel['unit']]
            start_samples = [row[si] for row in table.rows if si < len(row) and row[si].strip()]
            end_samples = [row[ei] for row in table.rows if ei < len(row) and row[ei].strip()]
            for r, row in enumerate(table.rows):
                if max(si, ei, di) >= len(row):
                    continue
                missing = [idx for idx in (si, ei, di) if row[idx].strip() == '']
                if len(missing) != 1:
                    continue
                start = _parse_dt(row[si])
                end = _parse_dt(row[ei])
                duration = _parse_num(row[di])
                c = missing[0]
                new = None
                if c == ei and start is not None and duration is not None:
                    new = _format_dt(start + timedelta(seconds=float(duration * unit_seconds)), end_samples)
                elif c == si and end is not None and duration is not None:
                    new = _format_dt(end - timedelta(seconds=float(duration * unit_seconds)), start_samples)
                elif c == di and start is not None and end is not None:
                    delta = Decimal(str((end - start).total_seconds())) / unit_seconds
                    new = _fmt_num(delta)
                if new is None:
                    continue
                out.issues.append(Issue(
                    self.name,
                    'missing_temporal_value_reconstructible',
                    f"Missing temporal value can be computed from stable elapsed-time relation ({rel['unit']}).",
                    'error', row=r, column=c, value='', repairable=repair,
                    metadata={'relation_id': rel['relation_id'], 'expected': new, 'unit': rel['unit']},
                ))
                if repair:
                    out.candidates.append(Candidate(
                        candidate_id=_id('temporal_missing', rel['relation_id'], r, c, new),
                        analyzer=self.name,
                        operation='set_cell',
                        reason='Compute one missing start/end/duration value from an exact stable elapsed-time relation.',
                        row=r, column=c, old_value='', new_value=new, cost=1, confidence=1.0, reversible=True,
                        metadata={'rule_type': 'temporal_missing_projection', 'projection': True, 'relation_id': rel['relation_id'], 'unit': rel['unit']},
                    ))
        return out
