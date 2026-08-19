from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from math import isfinite, log, sqrt
from random import Random
from statistics import median
from typing import Any

from .models import Table


def _num(value: str) -> float | None:
    s = value.strip().replace('−', '-')
    if not s:
        return None
    try:
        x = float(Decimal(s))
    except (InvalidOperation, ValueError, OverflowError):
        return None
    return x if isfinite(x) else None


def _numeric_columns(table: Table, min_n: int = 12, max_columns: int = 24) -> list[int]:
    out: list[int] = []
    for c in range(min(len(table.header), max_columns)):
        vals = [row[c] for row in table.rows if c < len(row) and row[c].strip()]
        if len(vals) < min_n:
            continue
        if sum(_num(v) is not None for v in vals) / len(vals) >= 0.9:
            out.append(c)
    return out


def _series(table: Table, c: int) -> list[tuple[int, float]]:
    out = []
    for r, row in enumerate(table.rows):
        if c >= len(row):
            continue
        x = _num(row[c])
        if x is not None:
            out.append((r, x))
    return out


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _variance(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)


def _acf1(xs: list[float]) -> float | None:
    if len(xs) < 4:
        return None
    m = _mean(xs)
    den = sum((x - m) ** 2 for x in xs)
    if den <= 0:
        return 0.0
    return sum((xs[i] - m) * (xs[i - 1] - m) for i in range(1, len(xs))) / den


def _eval(operation: str, vals: list[float]) -> float | None:
    try:
        if operation == 'sum' and len(vals) == 2:
            return vals[0] + vals[1]
        if operation == 'difference' and len(vals) == 2:
            return vals[0] - vals[1]
        if operation == 'product' and len(vals) == 2:
            return vals[0] * vals[1]
        if operation == 'ratio' and len(vals) == 2 and vals[1] != 0:
            return vals[0] / vals[1]
        if operation == 'sum3' and len(vals) == 3:
            return sum(vals)
        if operation == 'sum_minus' and len(vals) == 3:
            return vals[0] + vals[1] - vals[2]
        if operation == 'product_plus' and len(vals) == 3:
            return vals[0] * vals[1] + vals[2]
        if operation == 'product_minus' and len(vals) == 3:
            return vals[0] * vals[1] - vals[2]
    except (OverflowError, ZeroDivisionError):
        return None
    return None


def _relation_residuals(table: Table, rel: dict[str, Any]) -> list[tuple[int, float, float]]:
    try:
        ti = int(rel['target_index'])
        sis = [int(x) for x in rel['source_indexes']]
        op = str(rel['operation'])
    except (KeyError, TypeError, ValueError):
        return []
    out = []
    for r, row in enumerate(table.rows):
        if max([ti] + sis) >= len(row):
            continue
        y = _num(row[ti])
        xs = [_num(row[c]) for c in sis]
        if y is None or any(x is None for x in xs):
            continue
        expected = _eval(op, [float(x) for x in xs if x is not None])
        if expected is None or not isfinite(expected):
            continue
        out.append((r, y - expected, expected))
    return out


def _confidence_from_residuals(residuals: list[tuple[int, float, float]], abs_tol: float, rel_tol: float) -> float:
    if not residuals:
        return 0.0
    good = sum(abs(res) <= abs_tol + rel_tol * max(abs(exp), 1.0) for _, res, exp in residuals)
    return good / len(residuals)


def _cusum(xs: list[float]) -> tuple[int | None, float]:
    if len(xs) < 12:
        return None, 0.0
    m = _mean(xs)
    sd = sqrt(max(_variance(xs), 1e-30))
    cumulative = 0.0
    best = (None, 0.0)
    for i, x in enumerate(xs[:-1], start=1):
        cumulative += x - m
        score = abs(cumulative) / (sd * sqrt(len(xs)))
        if score > best[1]:
            best = (i, score)
    return best


def _sliding_drift(xs: list[float]) -> dict[str, Any]:
    n = len(xs)
    if n < 16:
        return {'applicable': False}
    w = max(6, n // 5)
    early = xs[:w]
    recent = xs[-w:]
    pooled = sqrt(max((_variance(early) + _variance(recent)) / 2.0, 1e-30))
    effect = abs(_mean(recent) - _mean(early)) / pooled
    alpha = 2.0 / (w + 1.0)
    ema = xs[0]
    for x in xs[1:]:
        ema = alpha * x + (1 - alpha) * ema
    return {
        'applicable': True,
        'window': w,
        'early_mean': _mean(early),
        'recent_mean': _mean(recent),
        'standardized_shift': effect,
        'exponential_mean': ema,
        'drift_flag': effect >= 2.5,
    }


def _recurring_regime(xs: list[float]) -> dict[str, Any]:
    if len(xs) < 24:
        return {'applicable': False}
    q = len(xs) // 4
    if q < 4:
        return {'applicable': False}
    means = [_mean(xs[i*q:(i+1)*q]) for i in range(3)] + [_mean(xs[3*q:])]
    spread = sqrt(max(_variance(xs), 1e-30))
    d13 = abs(means[0] - means[2]) / spread
    d24 = abs(means[1] - means[3]) / spread
    sep = abs((means[0] + means[2]) / 2 - (means[1] + means[3]) / 2) / spread
    return {
        'applicable': True,
        'quarter_means': means,
        'q1_q3_distance': d13,
        'q2_q4_distance': d24,
        'alternating_separation': sep,
        'recurring_regime_flag': d13 <= 0.6 and d24 <= 0.6 and sep >= 1.5,
    }


def _saturation(xs: list[float]) -> dict[str, Any]:
    if len(xs) < 12:
        return {'applicable': False}
    lo, hi = min(xs), max(xs)
    c = Counter(xs)
    lo_frac, hi_frac = c[lo] / len(xs), c[hi] / len(xs)
    return {
        'applicable': True,
        'minimum': lo,
        'maximum': hi,
        'minimum_fraction': lo_frac,
        'maximum_fraction': hi_frac,
        'lower_saturation_flag': lo_frac >= 0.08 and len(c) >= 4,
        'upper_saturation_flag': hi_frac >= 0.08 and len(c) >= 4,
    }


def _hysteresis_and_multistability(table: Table, xcol: int, ycol: int) -> tuple[dict[str, Any], dict[str, Any]]:
    pts = []
    for r, row in enumerate(table.rows):
        if max(xcol, ycol) >= len(row):
            continue
        x, y = _num(row[xcol]), _num(row[ycol])
        if x is not None and y is not None:
            pts.append((r, x, y))
    if len(pts) < 20:
        return {'applicable': False}, {'applicable': False}
    xs = [p[1] for p in pts]
    xmin, xmax = min(xs), max(xs)
    span = xmax - xmin
    if span <= 0:
        return {'applicable': False}, {'applicable': False}
    bins: dict[int, dict[str, list[float]]] = defaultdict(lambda: {'up': [], 'down': [], 'all': []})
    for i, (_, x, y) in enumerate(pts):
        b = min(9, max(0, int((x - xmin) / span * 10)))
        direction = 'up'
        if i > 0 and x < pts[i-1][1]:
            direction = 'down'
        bins[b][direction].append(y)
        bins[b]['all'].append(y)
    diffs = []
    multi = []
    for b, d in bins.items():
        if len(d['up']) >= 3 and len(d['down']) >= 3:
            pooled = sqrt(max((_variance(d['up']) + _variance(d['down'])) / 2.0, 1e-30))
            diffs.append(abs(_mean(d['up']) - _mean(d['down'])) / pooled)
        vals = d['all']
        if len(vals) >= 6:
            med = median(vals)
            lo = [v for v in vals if v <= med]
            hi = [v for v in vals if v > med]
            if len(lo) >= 2 and len(hi) >= 2:
                spread = sqrt(max(_variance(vals), 1e-30))
                sep = abs(_mean(hi) - _mean(lo)) / spread
                if sep >= 1.5:
                    multi.append({'bin': b, 'separation': sep, 'count': len(vals)})
    hyst = {
        'applicable': bool(diffs),
        'max_directional_separation': max(diffs) if diffs else 0.0,
        'hysteresis_flag': bool(diffs and max(diffs) >= 2.0),
    }
    multi_out = {
        'applicable': True,
        'multistable_bins': multi,
        'multistability_flag': bool(multi),
    }
    return hyst, multi_out


def _bootstrap_confidence(residuals: list[tuple[int, float, float]], abs_tol: float, rel_tol: float, seed: int = 731) -> dict[str, Any]:
    n = len(residuals)
    if n < 12:
        return {'applicable': False}
    rng = Random(seed)
    vals = []
    reps = 40
    for _ in range(reps):
        sample = [residuals[rng.randrange(n)] for _ in range(n)]
        vals.append(_confidence_from_residuals(sample, abs_tol, rel_tol))
    vals.sort()
    return {
        'applicable': True,
        'replicates': reps,
        'median_confidence': median(vals),
        'lower_95': vals[max(0, int(0.025 * reps) - 1)],
        'upper_95': vals[min(reps - 1, int(0.975 * reps))],
    }


def _kfold(residuals: list[tuple[int, float, float]], abs_tol: float, rel_tol: float, k: int = 5) -> dict[str, Any]:
    n = len(residuals)
    if n < max(15, k * 2):
        return {'applicable': False}
    folds = [[] for _ in range(k)]
    for i, item in enumerate(residuals):
        folds[i % k].append(item)
    scores = [_confidence_from_residuals(fold, abs_tol, rel_tol) for fold in folds if fold]
    return {
        'applicable': True,
        'k': len(scores),
        'fold_confidences': scores,
        'minimum_fold_confidence': min(scores),
        'mean_fold_confidence': _mean(scores),
    }


def _complexity_score(residuals: list[tuple[int, float, float]], parameter_count: int) -> dict[str, Any]:
    n = len(residuals)
    if n < 4:
        return {'applicable': False}
    rss = sum(res * res for _, res, _ in residuals)
    mse = max(rss / n, 1e-30)
    aic = n * log(mse) + 2 * parameter_count
    bic = n * log(mse) + parameter_count * log(n)
    return {'applicable': True, 'n': n, 'rss': rss, 'parameter_count': parameter_count, 'aic_like': aic, 'bic_like': bic}


def build_advanced_diagnostics(
    table: Table,
    numeric_registry: dict[str, Any] | None = None,
    relationship_registry: dict[str, Any] | None = None,
    config: Any | None = None,
) -> dict[str, Any]:
    """Conservative diagnostics used to gate repairs, never to invent truth by themselves."""
    numeric_registry = numeric_registry or {}
    relationship_registry = relationship_registry or {}
    min_rows = max(12, int(getattr(config, 'discovery_min_rows', 12))) if config is not None else 12
    abs_tol = float(getattr(config, 'numeric_abs_tolerance', 1e-6)) if config is not None else 1e-6
    rel_tol = float(getattr(config, 'numeric_rel_tolerance', 1e-6)) if config is not None else 1e-6
    ncols = _numeric_columns(table, min_n=min_rows)

    column_diagnostics: list[dict[str, Any]] = []
    change_points: list[dict[str, Any]] = []
    adaptive: list[dict[str, Any]] = []
    recurring: list[dict[str, Any]] = []
    saturation: list[dict[str, Any]] = []
    calibration_drift: list[dict[str, Any]] = []
    for c in ncols:
        pairs = _series(table, c)
        xs = [x for _, x in pairs]
        idx, score = _cusum(xs)
        cp = None
        if idx is not None:
            row = pairs[min(idx, len(pairs)-1)][0]
            cp = {'column': table.header[c], 'column_index': c, 'row': row, 'score': score, 'change_point_flag': score >= 1.25}
            change_points.append(cp)
        ad = _sliding_drift(xs) | {'column': table.header[c], 'column_index': c}
        rr = _recurring_regime(xs) | {'column': table.header[c], 'column_index': c}
        sat = _saturation(xs) | {'column': table.header[c], 'column_index': c}
        adaptive.append(ad)
        recurring.append(rr)
        saturation.append(sat)
        # Calibration/normalization drift: robust first/second-half median ratio and shift.
        if len(xs) >= 16:
            h = len(xs) // 2
            m1, m2 = median(xs[:h]), median(xs[h:])
            scale = (m2 / m1) if abs(m1) > 1e-15 else None
            sd = sqrt(max(_variance(xs), 1e-30))
            shift = abs(m2 - m1) / sd
            calibration_drift.append({
                'column': table.header[c], 'column_index': c,
                'first_half_median': m1, 'second_half_median': m2,
                'scale_ratio': scale, 'standardized_shift': shift,
                'drift_flag': shift >= 2.5 or (scale is not None and (abs(scale) >= 1.5 or abs(scale) <= 2/3)),
            })
        column_diagnostics.append({'column': table.header[c], 'rows': len(xs), 'change_point': cp, 'adaptive_window': ad, 'recurring_regime': rr, 'saturation': sat})

    hysteresis: list[dict[str, Any]] = []
    multistability: list[dict[str, Any]] = []
    for i, xcol in enumerate(ncols[:8]):
        for ycol in ncols[i+1:8]:
            h, m = _hysteresis_and_multistability(table, xcol, ycol)
            if h.get('applicable'):
                hysteresis.append({'x': table.header[xcol], 'y': table.header[ycol], **h})
            if m.get('applicable') and m.get('multistability_flag'):
                multistability.append({'x': table.header[xcol], 'y': table.header[ycol], **m})

    relation_diagnostics: list[dict[str, Any]] = []
    for rel in numeric_registry.get('relations', []):
        residuals = _relation_residuals(table, rel)
        rs = [x[1] for x in residuals]
        acf = _acf1(rs)
        n = len(rs)
        whiteness_bound = 2.0 / sqrt(n) if n else None
        whiteness_pass = bool(acf is not None and whiteness_bound is not None and abs(acf) <= whiteness_bound)
        bootstrap = _bootstrap_confidence(residuals, abs_tol, rel_tol)
        kfold = _kfold(residuals, abs_tol, rel_tol)
        complexity = _complexity_score(residuals, len(rel.get('sources', [])) + 1)
        # Sequential early evidence probe: confidence on expanding prefixes. It may reject expensive
        # candidates early, but final material repair still uses full-data certification elsewhere.
        prefixes = []
        for frac in (0.25, 0.5, 0.75, 1.0):
            k = max(1, int(len(residuals) * frac))
            prefixes.append({'fraction': frac, 'rows': k, 'confidence': _confidence_from_residuals(residuals[:k], abs_tol, rel_tol)})
        early_reject = bool(len(prefixes) >= 2 and prefixes[0]['rows'] >= 8 and prefixes[0]['confidence'] < 0.5 and prefixes[1]['confidence'] < 0.6)
        relation_diagnostics.append({
            'relation_id': rel.get('relation_id'),
            'operation': rel.get('operation'),
            'target': rel.get('target'),
            'sources': rel.get('sources', []),
            'residual_count': n,
            'residual_mean': _mean(rs) if rs else None,
            'residual_variance': _variance(rs) if rs else None,
            'lag1_autocorrelation': acf,
            'whiteness_bound': whiteness_bound,
            'residual_whiteness_pass': whiteness_pass,
            'residual_autocorrelation_flag': bool(acf is not None and whiteness_bound is not None and abs(acf) > whiteness_bound),
            'bootstrap_stability': bootstrap,
            'kfold_validation': kfold,
            'complexity_penalty': complexity,
            'sequential_evidence_probe': {'prefixes': prefixes, 'early_reject': early_reject, 'full_certification_required_for_repair': True},
        })

    # Epoch-aware revalidation: a discovered relation with violations concentrated after a detected
    # change point is marked for epoch scoping rather than trusted globally.
    epoch_revalidation = []
    cp_rows = [x['row'] for x in change_points if x.get('change_point_flag')]
    for rel in numeric_registry.get('relations', []):
        viol_rows = [int(v['row']) for v in rel.get('violations', [])]
        if not viol_rows or not cp_rows:
            continue
        best = None
        for cp in cp_rows:
            before = sum(r < cp for r in viol_rows)
            after = sum(r >= cp for r in viol_rows)
            concentration = max(before, after) / len(viol_rows)
            if best is None or concentration > best['concentration']:
                best = {'change_row': cp, 'violations_before': before, 'violations_after': after, 'concentration': concentration}
        if best and best['concentration'] >= 0.8:
            epoch_revalidation.append({'relation_id': rel.get('relation_id'), **best, 'epoch_scope_recommended': True})

    # Prefer simpler relations when fit is comparable; this does not certify truth, it only ranks candidates.
    ranked_complexity = sorted(
        [
            {'relation_id': d['relation_id'], **d['complexity_penalty']}
            for d in relation_diagnostics if d['complexity_penalty'].get('applicable')
        ],
        key=lambda x: (x['bic_like'], x['aic_like'], str(x['relation_id'])),
    )

    return {
        'enabled': True,
        'features': {
            'cusum_change_point': True,
            'sequential_evidence_probe': True,
            'adaptive_window_forgetting': True,
            'recurring_regime_recognition': True,
            'hysteresis_diagnostics': True,
            'multistability_preservation': True,
            'saturation_dead_zone_detection': True,
            'calibration_normalization_drift': True,
            'epoch_aware_revalidation': True,
            'residual_whiteness': True,
            'residual_autocorrelation': True,
            'bootstrap_relation_stability': True,
            'kfold_relation_validation': True,
            'complexity_penalty_ranking': True,
        },
        'column_diagnostics': column_diagnostics,
        'change_points': change_points,
        'adaptive_windows': adaptive,
        'recurring_regimes': recurring,
        'hysteresis': hysteresis,
        'multistability': multistability,
        'saturation': saturation,
        'calibration_drift': calibration_drift,
        'epoch_revalidation': epoch_revalidation,
        'relation_diagnostics': relation_diagnostics,
        'complexity_ranked_relations': ranked_complexity,
    }
