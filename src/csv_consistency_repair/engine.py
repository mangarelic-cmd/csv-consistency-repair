from __future__ import annotations

from ._version import __version__
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Iterable
from itertools import combinations
import hashlib
import json

from .analyzers import DEFAULT_ANALYZERS, analyze_all
from .io import file_sha256, read_table, write_table
from .models import Candidate, CycleRecord, Issue, Table, TableDialect
from .rules import RuleAnalyzer
from .discovery import DiscoveryAnalyzer, discover_relationships
from .numeric_constraints import NumericConstraintAnalyzer, discover_numeric_constraints
from .temporal import TemporalConstraintAnalyzer, discover_temporal_constraints
from .constraint_graph import build_constraint_graph
from .scope import ScopedRelationAnalyzer, discover_scoped_relations
from .sequential import SequentialConstraintAnalyzer, discover_sequential_constraints
from .contracts import validate_config
from .structural import augment_with_cross_analyzer_consensus, build_structural_diagnostics
from .advanced_diagnostics import build_advanced_diagnostics
from .maxima50 import MaximaRepairAnalyzer, build_maxima50_diagnostics
from .maxima55 import build_maxima55_diagnostics, explain_edits, next_evidence_from_issues, dry_run_plan
from .knowledge import (
    empty_knowledge_registry, update_knowledge_registry, relation_priority_map,
    candidate_knowledge_rank, public_knowledge_registry, candidate_relation_ids,
)


SEVERITY_WEIGHT = {"error": 10.0, "warning": 3.0, "info": 1.0}


@dataclass
class RepairConfig:
    remove_exact_duplicates: bool = False
    normalize_null_markers: bool = False
    normalize_booleans: bool = False
    auto_contextual_nulls: bool = False
    max_cycles: int = 8
    stable_cycles_required: int = 2
    dry_run: bool = False
    rules_path: str | None = None
    discover_relationships: bool = False
    repair_discovered_relationships: bool = False
    discovery_min_rows: int = 12
    discovery_min_group_support: int = 3
    discovery_confidence: float = 0.95
    discovery_min_coverage: float = 0.50
    discovery_stress_tolerance: float = 0.05
    discover_numeric_constraints: bool = False
    repair_numeric_constraints: bool = False
    numeric_min_independent_constraints: int = 2
    numeric_abs_tolerance: float = 0.000001
    numeric_rel_tolerance: float = 0.000001
    numeric_max_columns: int = 12
    numeric_max_formula_terms: int = 2
    numeric_missing_min_constraints: int = 1
    repair_missing_values: bool = False
    discovery_max_determinant_columns: int = 1
    discover_temporal_constraints: bool = False
    repair_temporal_missing: bool = False
    temporal_max_columns: int = 12
    discover_scoped_relations: bool = False
    repair_scoped_missing: bool = False
    repair_scoped_values: bool = False
    scope_min_rows: int = 12
    scope_confidence: float = 0.98
    scope_max_groups: int = 12
    discover_sequential_constraints: bool = False
    repair_sequential_missing: bool = False
    repair_sequential_values: bool = False
    sequential_min_rows: int = 16
    sequential_confidence: float = 0.99
    structural_consensus: bool = True
    structural_consensus_min_families: int = 2
    global_repair_plan: bool = True
    global_plan_max_candidates: int = 10
    global_plan_max_bundle: int = 4
    global_plan_max_trials: int = 220
    advanced_diagnostics: bool = True
    maxima50: bool = False
    maxima_repair: bool = False
    maxima_repair_headers: bool = False
    maxima_repair_row_alignment: bool = False
    maxima_repair_locale_numbers: bool = False
    maxima_repair_low_rank_missing: bool = False
    maxima_expression_terms: int = 5
    maxima55: bool = False
    safe_mode: bool = False
    auto_mode: bool = True
    cumulative_knowledge: bool = True


@dataclass
class RepairResult:
    input_path: str
    output_path: str | None
    report_path: str | None
    initial_score: float
    final_score: float
    final_status: str
    strong_stable: bool
    cycles: int
    committed_edits: int
    remaining_issues: int
    input_logical_digest: str
    output_logical_digest: str
    report: dict[str, Any]


def consistency_score(issues: Iterable[Issue]) -> float:
    return float(sum(SEVERITY_WEIGHT.get(i.severity, 1.0) for i in issues))


def _repair_objective(issues: Iterable[Issue]) -> tuple[float, float]:
    items = list(issues)
    repairable = float(sum(SEVERITY_WEIGHT.get(i.severity, 1.0) for i in items if i.repairable))
    total = consistency_score(items)
    return repairable, total


def _structural_signature(issues: list[Issue], candidates: list[Candidate]) -> str:
    payload = {
        "issues": sorted(repr(i.signature()) for i in issues),
        "candidates": sorted((c.analyzer, c.operation, c.conflict_key(), c.old_value, c.new_value, tuple(c.new_row or [])) for c in candidates),
    }
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _candidate_conflicts(candidates: list[Candidate]) -> set[str]:
    by_key: dict[tuple[Any, ...], list[Candidate]] = {}
    for c in candidates:
        by_key.setdefault(c.conflict_key(), []).append(c)
    conflicts: set[str] = set()
    for group in by_key.values():
        targets = {(c.operation, c.new_value, tuple(c.old_row or []), tuple(c.new_row or [])) for c in group}
        if len(targets) > 1:
            conflicts.update(c.candidate_id for c in group)
    return conflicts


def _apply_candidate(table: Table, candidate: Candidate) -> bool:
    if candidate.operation == "set_cell":
        if candidate.row is None or candidate.column is None:
            return False
        if candidate.row >= len(table.rows) or candidate.column >= len(table.rows[candidate.row]):
            return False
        if table.rows[candidate.row][candidate.column] != candidate.old_value:
            return False
        table.rows[candidate.row][candidate.column] = candidate.new_value or ""
        return True
    if candidate.operation == "rename_column":
        if candidate.column is None or candidate.column >= len(table.header):
            return False
        if table.header[candidate.column] != candidate.old_value:
            return False
        new_value = candidate.new_value or ""
        if new_value in table.header and new_value != candidate.old_value:
            return False
        table.header[candidate.column] = new_value
        return True
    if candidate.operation == "replace_row":
        if candidate.row is None or candidate.row >= len(table.rows) or candidate.old_row is None or candidate.new_row is None:
            return False
        if table.rows[candidate.row] != candidate.old_row:
            return False
        table.rows[candidate.row] = list(candidate.new_row)
        return True
    if candidate.operation == "delete_row":
        if candidate.row is None or candidate.row >= len(table.rows):
            return False
        if candidate.old_row is not None and table.rows[candidate.row] != candidate.old_row:
            return False
        del table.rows[candidate.row]
        return True
    return False


def _inverse_candidate(table: Table, edit: dict[str, Any]) -> bool:
    operation = edit["operation"]
    if operation == "set_cell":
        r, c = edit["row"], edit["column"]
        if r is None or c is None or r >= len(table.rows) or c >= len(table.rows[r]):
            return False
        if table.rows[r][c] != (edit.get("new_value") or ""):
            return False
        table.rows[r][c] = edit.get("old_value") or ""
        return True
    if operation == "rename_column":
        c = edit["column"]
        if c is None or c >= len(table.header):
            return False
        if table.header[c] != (edit.get("new_value") or ""):
            return False
        table.header[c] = edit.get("old_value") or ""
        return True
    if operation == "replace_row":
        r = edit.get("row")
        if r is None or r >= len(table.rows): return False
        new_row = edit.get("new_row") or edit.get("metadata", {}).get("new_row")
        old_row = edit.get("old_row")
        if new_row is None or old_row is None or table.rows[r] != list(new_row): return False
        table.rows[r] = list(old_row)
        return True
    if operation == "delete_row":
        r = edit["row"]
        if r is None or r > len(table.rows):
            return False
        old_row = edit.get("old_row")
        if old_row is None:
            return False
        table.rows.insert(r, list(old_row))
        return True
    return False


def _forward_edit(table: Table, edit: dict[str, Any]) -> bool:
    operation = edit.get("operation")
    if operation == "set_cell":
        r, c = edit.get("row"), edit.get("column")
        if r is None or c is None or r >= len(table.rows) or c >= len(table.rows[r]):
            return False
        if table.rows[r][c] != (edit.get("old_value") or ""):
            return False
        table.rows[r][c] = edit.get("new_value") or ""
        return True
    if operation == "rename_column":
        c = edit.get("column")
        if c is None or c >= len(table.header):
            return False
        if table.header[c] != (edit.get("old_value") or ""):
            return False
        new_value = edit.get("new_value") or ""
        if new_value in table.header and new_value != table.header[c]:
            return False
        table.header[c] = new_value
        return True
    if operation == "replace_row":
        r = edit.get("row")
        old_row = edit.get("old_row"); new_row = edit.get("new_row") or edit.get("metadata", {}).get("new_row")
        if r is None or r >= len(table.rows) or old_row is None or new_row is None or table.rows[r] != list(old_row): return False
        table.rows[r] = list(new_row)
        return True
    if operation == "delete_row":
        r = edit.get("row")
        if r is None or r >= len(table.rows):
            return False
        old_row = edit.get("old_row")
        if old_row is not None and table.rows[r] != old_row:
            return False
        del table.rows[r]
        return True
    return False


def _candidate_sort_key(c: Candidate, config: RepairConfig | None = None) -> tuple[Any, ...]:
    # Low structural cost, then cumulative convergence knowledge, then high confidence.
    # Knowledge is an ordering hint only; it never bypasses shadow validation or the
    # global objective-improvement requirement.
    priorities = getattr(config, "_knowledge_priority", None) if config is not None else None
    knowledge_rank = candidate_knowledge_rank(c.metadata, priorities)
    return (c.cost, knowledge_rank, -c.confidence, c.analyzer, c.candidate_id)




def _shadow_global_plan(
    table: Table,
    analysis: Any,
    candidates: list[Candidate],
    conflicts: set[str],
    config: RepairConfig,
    active_analyzers,
) -> dict[str, Any]:
    """Bounded shadow search for a minimum-edit globally improving repair bundle.

    Candidate generation remains conservative and domain-specific. This layer only tests
    combinations already proposed by analyzers; it never invents a new cell value.
    """
    baseline = _repair_objective(analysis.issues)
    eligible = [
        c for c in candidates
        if c.candidate_id not in conflicts and c.operation in {'set_cell', 'rename_column', 'delete_row', 'replace_row'}
    ][:max(1, int(config.global_plan_max_candidates))]
    # Fast path for a large set of mutually non-conflicting edits.  Test the whole
    # materializable set once before expensive O(n^k) counterfactual enumeration.  If it
    # strictly improves the global objective, no smaller search can close *more* of the
    # repairable objective in this cycle; the engine still preserves reversibility and
    # performs full post-apply replay later.
    if len(eligible) >= 4:
        keys=[c.conflict_key() for c in eligible]
        if len(set(keys)) == len(keys):
            wide=table.clone(); applied=[]
            for c in eligible:
                if _apply_candidate(wide,c): applied.append(c)
            if len(applied)>=4:
                a=_analyze_table(wide,config,active_analyzers)
                obj=_repair_objective(a.issues)
                if obj < baseline:
                    return {
                        'enabled': True, 'baseline_objective': list(baseline),
                        'eligible_candidates': len(eligible), 'counterfactual_candidates': [],
                        'bundle_trials': 1, 'selected_candidate_ids':[c.candidate_id for c in applied],
                        'selected_size':len(applied), 'selected_objective':list(obj),
                        'fast_wide_plan':True, '_selected_candidates':applied, '_selected_trial':wide,
                    }

    counterfactual = []
    for c in eligible:
        trial = table.clone()
        if not _apply_candidate(trial, c):
            counterfactual.append({'candidate_id': c.candidate_id, 'valid': False})
            continue
        a = _analyze_table(trial, config, active_analyzers)
        obj = _repair_objective(a.issues)
        counterfactual.append({
            'candidate_id': c.candidate_id, 'valid': True,
            'objective_before': list(baseline), 'objective_after': list(obj),
            'globally_improves': obj < baseline,
        })

    best_combo: tuple[Candidate, ...] | None = None
    best_obj: tuple[float, float] | None = None
    best_trial: Table | None = None
    trials = 0
    max_bundle = max(2, min(int(config.global_plan_max_bundle), len(eligible))) if len(eligible) >= 2 else 1
    trial_cap = max(1, int(config.global_plan_max_trials))
    for k in range(2, max_bundle + 1):
        for combo in combinations(eligible, k):
            if trials >= trial_cap:
                break
            keys = [c.conflict_key() for c in combo]
            if len(set(keys)) != len(keys):
                continue
            trials += 1
            trial = table.clone()
            if not all(_apply_candidate(trial, c) for c in combo):
                continue
            a = _analyze_table(trial, config, active_analyzers)
            obj = _repair_objective(a.issues)
            if obj >= baseline:
                continue
            if best_obj is None or (obj[0], obj[1], k, tuple(c.candidate_id for c in combo)) < (best_obj[0], best_obj[1], len(best_combo or ()), tuple(c.candidate_id for c in (best_combo or ()))):
                best_combo, best_obj, best_trial = combo, obj, trial
        if trials >= trial_cap:
            break
    return {
        'enabled': True,
        'baseline_objective': list(baseline),
        'eligible_candidates': len(eligible),
        'counterfactual_candidates': counterfactual,
        'bundle_trials': trials,
        'selected_candidate_ids': [c.candidate_id for c in best_combo] if best_combo else [],
        'selected_size': len(best_combo or ()),
        'selected_objective': list(best_obj) if best_obj is not None else None,
        '_selected_candidates': list(best_combo or ()),
        '_selected_trial': best_trial,
    }


def _analyze_table(table: Table, config: RepairConfig, active_analyzers) -> Any:
    # Per-repair memoization: analyzers are deterministic on a frozen table and config.
    # Shadow planning used to recompute the entire discovery stack many times for the same
    # state; caching by logical digest preserves semantics while removing that multiplicative
    # cost.  The cache is attached dynamically so it never appears in public config output.
    cache = getattr(config, '_analysis_cache', None)
    key = table.logical_digest() if cache is not None else None
    if cache is not None and key in cache:
        return cache[key]
    base = analyze_all(table, config, active_analyzers)
    result = augment_with_cross_analyzer_consensus(base, table, config)
    if cache is not None and key is not None:
        if len(cache) >= 64:
            cache.pop(next(iter(cache)))
        cache[key] = result
    return result


def _discovery_registries(table: Table, config: RepairConfig) -> dict[str, dict[str, Any]]:
    # Discovery registries are deterministic on a frozen state.  Cache them because the
    # convergence knowledge layer observes the same state that analyzers/reporting later
    # revisit.  This makes the new memory layer mostly bookkeeping rather than duplicate
    # discovery work.
    cache = getattr(config, "_registry_cache", None)
    key = table.logical_digest() if cache is not None else None
    if cache is not None and key in cache:
        return cache[key]
    relationship_registry = discover_relationships(table, config) if config.discover_relationships else {
        "enabled": False,
        "relationship_count": 0,
        "stable_relationships": 0,
        "unstable_relationships": 0,
        "relationships": [],
        "unique_key_candidates": [],
    }
    out = {
        "relationship": relationship_registry,
        "numeric": discover_numeric_constraints(table, config),
        "temporal": discover_temporal_constraints(table, config),
        "scoped": discover_scoped_relations(table, config),
        "sequential": discover_sequential_constraints(table, config),
    }
    if cache is not None and key is not None:
        if len(cache) >= 32:
            cache.pop(next(iter(cache)))
        cache[key] = out
    return out


def _auto_configure(table: Table, config: RepairConfig) -> RepairConfig:
    """Enable only high-yield analyzers when the caller uses the ordinary default mode.

    This keeps zero-config useful without turning every file into the expensive full safe-mode
    search.  Explicit advanced configuration remains authoritative; users can also set
    ``auto_mode=False`` for the historical minimal behavior.
    """
    if not getattr(config, 'auto_mode', True):
        return config
    advanced = any((
        config.discover_relationships, config.discover_numeric_constraints,
        config.discover_temporal_constraints, config.discover_scoped_relations,
        config.discover_sequential_constraints, config.maxima_repair, config.maxima50,
        config.maxima55, bool(config.rules_path),
    ))
    if advanced and not config.safe_mode:
        return config
    n=len(table.rows); w=len(table.header)
    if not n or not w:
        return replace(config, normalize_booleans=True)

    def numericish(v: str) -> bool:
        t=v.strip().replace('−','-')
        if not t: return False
        try: float(t); return True
        except ValueError: return False

    numeric_cols=[]; categorical=[]; blank_cells=0; marker_cells=0
    for c in range(w):
        vals=[row[c] for row in table.rows if c < len(row)]
        non=[v for v in vals if v.strip()]
        blank_cells += sum(not v.strip() for v in vals)
        marker_cells += sum(v.strip().casefold() in {'na','n/a','null','none','nil','missing'} for v in vals)
        if len(non)>=max(4,min(12,n//2)) and sum(numericish(v) for v in non)/len(non)>=.9:
            numeric_cols.append(c)
        uniq={v for v in non}
        if 2 <= len(uniq) <= min(20,max(2,n//3)):
            categorical.append(c)

    names=[h.strip().casefold() for h in table.header]
    joined=' '.join(names)
    row_mismatch=any(len(r)!=w for r in table.rows)
    header_dirty=any(h != h.strip() for h in table.header) and len({h.strip() for h in table.header}) == len(table.header)
    locale_signal=any(',' in v and any(ch.isdigit() for ch in v) for row in table.rows[:200] for v in row)
    has_temporal = any(tok in joined for tok in ('start','end','duration','timestamp','date','time'))
    has_sequential = any(tok in joined for tok in ('balance','credit','debit','inventory','stock','cumulative'))
    categorical_text=[c for c in categorical if c not in numeric_cols]

    if config.safe_mode:
        # Safe mode remains broad, but it no longer runs families that the table cannot
        # materially support. This prevents numeric-only tables from spending time on
        # repeated categorical mappings and avoids treating periodic numeric values as
        # a second independent evidence family. Full diagnostics are still emitted on a
        # stratified report sample.
        numeric_ok = n >= 12 and len(numeric_cols) >= 3
        mapping_ok = n >= 12 and len(categorical_text) >= 1 and (blank_cells > 0 or len(categorical_text) >= 2)
        scoped_ok = n >= 24 and len(categorical_text) >= 1 and len(numeric_cols) >= 2
        row_ok = row_mismatch
        header_ok = header_dirty
        locale_ok = locale_signal
        lowrank_ok = blank_cells > 0 and len(numeric_cols) >= 3
        maxima_any = row_ok or header_ok or locale_ok or lowrank_ok
        return replace(
            config,
            discover_relationships=mapping_ok, repair_discovered_relationships=mapping_ok,
            discover_numeric_constraints=numeric_ok, repair_numeric_constraints=numeric_ok,
            discover_temporal_constraints=has_temporal, repair_temporal_missing=has_temporal,
            discover_scoped_relations=scoped_ok, repair_scoped_missing=scoped_ok, repair_scoped_values=scoped_ok,
            discover_sequential_constraints=has_sequential and len(numeric_cols)>=2,
            repair_sequential_missing=has_sequential and len(numeric_cols)>=2,
            repair_sequential_values=has_sequential and len(numeric_cols)>=2,
            maxima_repair=maxima_any, maxima_repair_row_alignment=row_ok, maxima_repair_headers=header_ok,
            maxima_repair_locale_numbers=locale_ok, maxima_repair_low_rank_missing=lowrank_ok,
        )

    kwargs=dict(normalize_booleans=True)
    # Null markers are only normalized automatically when the table already uses genuine
    # empty cells as the same missing representation; the analyzer applies a second
    # per-column contextual gate before materializing an edit.
    if marker_cells and blank_cells and not config.normalize_null_markers:
        kwargs['normalize_null_markers']=True
        kwargs['auto_contextual_nulls']=True
    if n>=12 and len(numeric_cols)>=3:
        kwargs.update(discover_numeric_constraints=True, repair_numeric_constraints=True, repair_missing_values=True)
    if n>=12 and has_temporal:
        kwargs.update(discover_temporal_constraints=True, repair_temporal_missing=True, repair_missing_values=True)
    if n>=16 and has_sequential and len(numeric_cols)>=2:
        kwargs.update(discover_sequential_constraints=True, repair_sequential_missing=True, repair_sequential_values=True, repair_missing_values=True)
    if n>=12 and blank_cells and len(categorical)>=2:
        kwargs.update(discover_relationships=True, repair_discovered_relationships=True, repair_missing_values=True, discovery_max_determinant_columns=2)
    if n>=24 and categorical and len(numeric_cols)>=2:
        kwargs.update(discover_scoped_relations=True, repair_scoped_missing=True, repair_scoped_values=True, repair_missing_values=True)

    # Maxima edit surfaces are enabled only when a cheap structural probe says they can
    # plausibly matter.  Full Maxima diagnostics remain disabled in auto mode so report
    # generation does not dominate normal repairs.
    maxima_flags={}
    if row_mismatch:
        maxima_flags['maxima_repair_row_alignment']=True
    if header_dirty:
        maxima_flags['maxima_repair_headers']=True
    if locale_signal:
        maxima_flags['maxima_repair_locale_numbers']=True
    if blank_cells and len(numeric_cols)>=3:
        maxima_flags['maxima_repair_low_rank_missing']=True
    if maxima_flags:
        kwargs['maxima_repair']=True
        kwargs.update(maxima_flags)
    return replace(config, **kwargs)


def _diagnostic_sample(table: Table, limit: int = 128) -> tuple[Table, bool]:
    if len(table.rows) <= limit:
        return table, False
    # Deterministic stratified sample spanning the full row domain, not a convenient window.
    idx=sorted({round(i*(len(table.rows)-1)/(limit-1)) for i in range(limit)})
    sampled=Table(list(table.header),[list(table.rows[i]) for i in idx],table.dialect,table.encoding,table.utf8_bom)
    return sampled, True


def repair(
    input_path: str | Path,
    output_path: str | Path | None = None,
    report_path: str | Path | None = None,
    config: RepairConfig | None = None,
    analyzers=DEFAULT_ANALYZERS,
) -> RepairResult:
    config = validate_config(config or RepairConfig())
    input_path = Path(input_path)
    if not input_path.is_file():
        raise ValueError(f"Input CSV does not exist: {input_path}")
    input_file_digest = file_sha256(input_path)
    table = read_table(input_path)
    config = _auto_configure(table, config)
    config._analysis_cache = {}  # type: ignore[attr-defined]
    config._registry_cache = {}  # type: ignore[attr-defined]
    original_table = table.clone()
    original_format_contract = original_table.format_contract()
    active_analyzers = tuple(analyzers)
    if config.rules_path:
        active_analyzers = active_analyzers + (RuleAnalyzer.from_path(config.rules_path),)
    if config.discover_relationships:
        active_analyzers = active_analyzers + (DiscoveryAnalyzer(),)
    if config.discover_numeric_constraints:
        active_analyzers = active_analyzers + (NumericConstraintAnalyzer(),)
    if config.discover_temporal_constraints:
        active_analyzers = active_analyzers + (TemporalConstraintAnalyzer(),)
    if config.discover_scoped_relations:
        active_analyzers = active_analyzers + (ScopedRelationAnalyzer(),)
    if config.discover_sequential_constraints:
        active_analyzers = active_analyzers + (SequentialConstraintAnalyzer(),)
    if config.maxima_repair:
        active_analyzers = active_analyzers + (MaximaRepairAnalyzer(),)
    initial_digest = table.logical_digest()
    initial_analysis = _analyze_table(table, config, active_analyzers)
    initial_score = consistency_score(initial_analysis.issues)

    knowledge_state = empty_knowledge_registry() if config.cumulative_knowledge else {"enabled": False, "relations": {}, "timeline": [], "summary": {}}
    if config.cumulative_knowledge:
        update_knowledge_registry(
            knowledge_state, initial_analysis.evidence, cycle=0,
            state_digest=table.logical_digest(), edits_since_previous=[],
        )
        config._knowledge_priority = relation_priority_map(knowledge_state)  # type: ignore[attr-defined]

    cycle_records: list[CycleRecord] = []
    committed: list[dict[str, Any]] = []
    rejected_global: list[dict[str, Any]] = []
    global_plan_ledger: list[dict[str, Any]] = []
    stable_streak = 0
    previous_signature: str | None = None

    for cycle in range(1, max(1, config.max_cycles) + 1):
        analysis_before = _analyze_table(table, config, active_analyzers)
        score_before = consistency_score(analysis_before.issues)
        signature = _structural_signature(analysis_before.issues, analysis_before.candidates)
        conflicts = _candidate_conflicts(analysis_before.candidates)

        accepted_cycle: list[dict[str, Any]] = []
        rejected_cycle: list[dict[str, Any]] = []
        candidates = sorted(analysis_before.candidates, key=lambda c: _candidate_sort_key(c, config))

        batch_committed = False
        plan = _shadow_global_plan(table, analysis_before, candidates, conflicts, config, active_analyzers) if config.global_repair_plan else {
            'enabled': False, 'selected_candidate_ids': [], 'selected_size': 0, '_selected_candidates': [], '_selected_trial': None, 'counterfactual_candidates': []
        }
        public_plan = {k: v for k, v in plan.items() if not k.startswith('_')}
        public_plan['cycle'] = cycle
        global_plan_ledger.append(public_plan)
        selected = plan.get('_selected_candidates') or []
        selected_trial = plan.get('_selected_trial')
        selected_decision = 'committed_global_minimum_edit_plan'
        # Verification PASS014 exposed starvation when a bounded 2-4 edit plan was
        # repeatedly preferred over a much larger mutually compatible batch.  Test the
        # full non-destructive candidate set in one shadow state and prefer it whenever
        # it closes strictly more of the global objective.  Row deletions stay excluded
        # here because they shift coordinates; they continue through the ordered path.
        wide_candidates = [
            c for c in candidates
            if c.candidate_id not in conflicts and c.operation in {'set_cell', 'rename_column', 'replace_row'}
        ]
        if len(wide_candidates) >= 4:
            wide_trial = table.clone()
            wide_applied: list[Candidate] = []
            for c in wide_candidates:
                if _apply_candidate(wide_trial, c):
                    wide_applied.append(c)
            if len(wide_applied) >= 4:
                wide_analysis = _analyze_table(wide_trial, config, active_analyzers)
                wide_obj = _repair_objective(wide_analysis.issues)
                selected_obj = _repair_objective(_analyze_table(selected_trial, config, active_analyzers).issues) if selected_trial is not None else _repair_objective(analysis_before.issues)
                if wide_obj < selected_obj:
                    selected, selected_trial = wide_applied, wide_trial
                    selected_decision = 'committed_certified_wide_batch'
                    public_plan['wide_batch_override'] = True
                    public_plan['wide_batch_size'] = len(wide_applied)
                    public_plan['wide_batch_objective'] = list(wide_obj)
        if len(selected) >= 2 and selected_trial is not None:
            selected_analysis = _analyze_table(selected_trial, config, active_analyzers)
            selected_score = consistency_score(selected_analysis.issues)
            if config.dry_run:
                for candidate in selected:
                    rejected = candidate.to_dict() | {
                        'decision': 'dry_run_would_commit_global_plan',
                        'plan_size': len(selected),
                        'score_before': score_before,
                        'score_after': selected_score,
                    }
                    rejected_cycle.append(rejected)
                    rejected_global.append(rejected)
            else:
                table = selected_trial
                plan_id = hashlib.sha1(''.join(c.candidate_id for c in selected).encode('utf-8')).hexdigest()[:16]
                for candidate in selected:
                    accepted = candidate.to_dict() | {
                        'decision': selected_decision,
                        'plan_id': plan_id,
                        'plan_size': len(selected),
                        'score_before': score_before,
                        'score_after': selected_score,
                    }
                    accepted_cycle.append(accepted)
                    committed.append(accepted)
                batch_committed = True

        # If bounded combinatorial search does not select a bundle, keep the fast certified
        # all-candidate batch path for large, mutually non-conflicting repair sets.
        if not batch_committed and not config.dry_run:
            batch_candidates = [
                c for c in candidates
                if c.operation == 'set_cell' and c.candidate_id not in conflicts
            ]
            if len(batch_candidates) >= 4:
                batch_trial = table.clone()
                batch_applied: list[Candidate] = []
                for candidate in batch_candidates:
                    if _apply_candidate(batch_trial, candidate):
                        batch_applied.append(candidate)
                if len(batch_applied) >= 4:
                    batch_analysis = _analyze_table(batch_trial, config, active_analyzers)
                    batch_score = consistency_score(batch_analysis.issues)
                    if _repair_objective(batch_analysis.issues) < _repair_objective(analysis_before.issues):
                        table = batch_trial
                        batch_id = hashlib.sha1(''.join(c.candidate_id for c in batch_applied).encode('utf-8')).hexdigest()[:16]
                        for candidate in batch_applied:
                            accepted = candidate.to_dict() | {
                                'decision': 'committed_certified_batch',
                                'batch_id': batch_id,
                                'batch_size': len(batch_applied),
                                'score_before': score_before,
                                'score_after': batch_score,
                            }
                            accepted_cycle.append(accepted)
                            committed.append(accepted)
                        batch_committed = True

        for candidate in ([] if batch_committed else candidates):
            trial = table.clone()
            if not _apply_candidate(trial, candidate):
                rejected = candidate.to_dict() | {"decision": "rejected_stale_or_invalid"}
                rejected_cycle.append(rejected)
                rejected_global.append(rejected)
                continue
            trial_analysis = _analyze_table(trial, config, active_analyzers)
            score_after_candidate = consistency_score(trial_analysis.issues)
            current_analysis = _analyze_table(table, config, active_analyzers)
            current_score = consistency_score(current_analysis.issues)
            if _repair_objective(trial_analysis.issues) < _repair_objective(current_analysis.issues):
                if config.dry_run:
                    rejected = candidate.to_dict() | {
                        "decision": "dry_run_would_commit",
                        "score_before": current_score,
                        "score_after": score_after_candidate,
                    }
                    rejected_cycle.append(rejected)
                    rejected_global.append(rejected)
                else:
                    if _apply_candidate(table, candidate):
                        accepted = candidate.to_dict() | {
                            "decision": "committed",
                            "score_before": current_score,
                            "score_after": score_after_candidate,
                        }
                        accepted_cycle.append(accepted)
                        committed.append(accepted)
            else:
                # A locally non-improving edit may expose a second deterministic repair.
                # Test the two-step composition atomically on the whole table before committing either step.
                trial_analysis2 = _analyze_table(trial, config, active_analyzers)
                trial_conflicts = _candidate_conflicts(trial_analysis2.candidates)
                second = None
                second_score = None
                composed_trial = None
                for candidate2 in sorted(trial_analysis2.candidates, key=lambda c: _candidate_sort_key(c, config)):
                    trial2 = trial.clone()
                    if not _apply_candidate(trial2, candidate2):
                        continue
                    analysis2 = _analyze_table(trial2, config, active_analyzers)
                    score2 = consistency_score(analysis2.issues)
                    if _repair_objective(analysis2.issues) < _repair_objective(current_analysis.issues):
                        second = candidate2
                        second_score = score2
                        composed_trial = trial2
                        break
                if second is not None and composed_trial is not None:
                    if config.dry_run:
                        rejected = candidate.to_dict() | {
                            "decision": "dry_run_would_commit_composition",
                            "score_before": current_score,
                            "score_after_step_1": score_after_candidate,
                            "score_after_step_2": second_score,
                            "second_candidate": second.to_dict(),
                        }
                        rejected_cycle.append(rejected)
                        rejected_global.append(rejected)
                    else:
                        # Commit atomically by replacing the working table only after both steps succeeded on the clone.
                        table = composed_trial
                        composition_id = hashlib.sha1((candidate.candidate_id + second.candidate_id).encode("utf-8")).hexdigest()[:16]
                        accepted1 = candidate.to_dict() | {
                            "decision": "committed_composed_step_1",
                            "composition_id": composition_id,
                            "score_before": current_score,
                            "score_after": score_after_candidate,
                        }
                        accepted2 = second.to_dict() | {
                            "decision": "committed_composed_step_2",
                            "composition_id": composition_id,
                            "score_before": score_after_candidate,
                            "score_after": second_score,
                        }
                        accepted_cycle.extend([accepted1, accepted2])
                        committed.extend([accepted1, accepted2])
                else:
                    rejected = candidate.to_dict() | {
                        "decision": "rejected_no_global_improvement",
                        "score_before": current_score,
                        "score_after": score_after_candidate,
                    }
                    rejected_cycle.append(rejected)
                    rejected_global.append(rejected)

        analysis_after = _analyze_table(table, config, active_analyzers)
        score_after = consistency_score(analysis_after.issues)
        after_signature = _structural_signature(analysis_after.issues, analysis_after.candidates)

        # Observe the corrected state before the next cycle.  The cumulative registry
        # keeps weak->strong trajectories and provenance across cycles instead of
        # discarding what was learned when discovery reruns.
        if config.cumulative_knowledge:
            update_knowledge_registry(
                knowledge_state, analysis_after.evidence, cycle=cycle,
                state_digest=table.logical_digest(), edits_since_previous=accepted_cycle,
            )
            config._knowledge_priority = relation_priority_map(knowledge_state)  # type: ignore[attr-defined]
            priorities = getattr(config, "_knowledge_priority", {})
            for edit in accepted_cycle:
                ids = candidate_relation_ids(edit.get("metadata"))
                if ids:
                    edit["knowledge_support"] = {
                        "relation_ids": ids,
                        "best_rank": min((priorities.get(rid, 4) for rid in ids), default=4),
                        "authority": "ordering_and_provenance_only",
                    }

        no_change = not accepted_cycle
        same_structure = previous_signature == after_signature
        if no_change and (same_structure or previous_signature is None and signature == after_signature):
            stable_streak += 1
        else:
            stable_streak = 0
        previous_signature = after_signature

        cycle_records.append(CycleRecord(
            cycle=cycle,
            score_before=score_before,
            score_after=score_after,
            issue_count_before=len(analysis_before.issues),
            issue_count_after=len(analysis_after.issues),
            candidate_count=len(candidates),
            accepted=accepted_cycle,
            rejected=rejected_cycle,
            structural_signature=after_signature,
            stable_streak=stable_streak,
        ))

        if stable_streak >= config.stable_cycles_required:
            break

    final_analysis = _analyze_table(table, config, active_analyzers)
    final_score = consistency_score(final_analysis.issues)
    strong_stable = stable_streak >= config.stable_cycles_required
    repairable_remaining = [i for i in final_analysis.issues if i.repairable]

    forward_replay_pass = None
    inverse_roundtrip_pass = None
    if not config.dry_run:
        replay = original_table.clone()
        forward_replay_pass = all(_forward_edit(replay, edit) for edit in committed) and replay.logical_digest() == table.logical_digest()
        inverse = table.clone()
        inverse_roundtrip_pass = all(_inverse_candidate(inverse, edit) for edit in reversed(committed)) and inverse.logical_digest() == initial_digest

    if not config.dry_run and (not forward_replay_pass or not inverse_roundtrip_pass):
        final_status = "REPLAY_FAILED"
    elif repairable_remaining:
        final_status = "OPEN_REPAIRABLE"
    elif final_analysis.issues:
        final_status = "STABLE_WITH_REPORTED_ISSUES" if strong_stable else "LIMIT_REACHED_WITH_REPORTED_ISSUES"
    else:
        final_status = "PASS" if strong_stable else "CLEAN_LIMIT_REACHED"

    final_registries = _discovery_registries(table, config)
    relationship_registry = final_registries["relationship"]
    numeric_registry = final_registries["numeric"]
    temporal_registry = final_registries["temporal"]
    scoped_registry = final_registries["scoped"]
    sequential_registry = final_registries["sequential"]
    initial_registries = _discovery_registries(original_table, config)
    structural_before = build_structural_diagnostics(
        original_table, initial_analysis,
        initial_registries["relationship"], initial_registries["numeric"],
        initial_registries["scoped"], initial_registries["sequential"], initial_registries["temporal"],
    )
    structural_after = build_structural_diagnostics(
        table, final_analysis, relationship_registry, numeric_registry, scoped_registry, sequential_registry, temporal_registry
    )
    advanced_before = build_advanced_diagnostics(original_table, initial_registries['numeric'], initial_registries['relationship'], config) if config.advanced_diagnostics else {'enabled': False}
    advanced_after = build_advanced_diagnostics(table, numeric_registry, relationship_registry, config) if config.advanced_diagnostics else {'enabled': False}
    if config.maxima50:
        max_before_table, sampled_before = _diagnostic_sample(original_table, 128 if config.safe_mode else max(128, len(original_table.rows)))
        max_after_table, sampled_after = _diagnostic_sample(table, 128 if config.safe_mode else max(128, len(table.rows)))
        maxima_before = build_maxima50_diagnostics(max_before_table, config)
        if table.logical_digest() == original_table.logical_digest():
            maxima_after = maxima_before
        else:
            maxima_after = build_maxima50_diagnostics(max_after_table, config)
        for packet, sampled, fulln in ((maxima_before,sampled_before,len(original_table.rows)),(maxima_after,sampled_after,len(table.rows))):
            if isinstance(packet,dict):
                packet['diagnostic_sampling']={'sampled':bool(sampled),'sample_rows':len(max_before_table.rows) if packet is maxima_before else len(max_after_table.rows),'full_rows':fulln,'strategy':'deterministic_stratified'}
    else:
        maxima_before = maxima_after = {'enabled': False}
    constraint_graph = build_constraint_graph(table, relationship_registry, numeric_registry, scoped_registry, sequential_registry)
    maxima55_before = build_maxima55_diagnostics(original_table, constraint_graph=build_constraint_graph(original_table, initial_registries["relationship"], initial_registries["numeric"], initial_registries["scoped"], initial_registries["sequential"]), advanced=advanced_before, maxima50=maxima_before, input_path=input_path) if config.maxima55 else {'enabled': False}
    if config.maxima55 and table.logical_digest() == original_table.logical_digest():
        maxima55_after = maxima55_before
    else:
        maxima55_after = build_maxima55_diagnostics(table, constraint_graph=constraint_graph, advanced=advanced_after, maxima50=maxima_after, input_path=None) if config.maxima55 else {'enabled': False}
    closure = {
        "configured_scope_clean": final_score == 0 and not repairable_remaining,
        "two_cycle_stability_pass": strong_stable,
        "forward_replay_pass": forward_replay_pass,
        "inverse_roundtrip_pass": inverse_roundtrip_pass,
        "format_preservation_pass": original_format_contract == table.format_contract(),
        "relationship_stability_checked": bool(config.discover_relationships),
        "numeric_constraint_stability_checked": bool(config.discover_numeric_constraints),
        "temporal_constraint_stability_checked": bool(config.discover_temporal_constraints),
        "scoped_relation_stability_checked": bool(config.discover_scoped_relations),
        "sequential_constraint_stability_checked": bool(config.discover_sequential_constraints),
        "stable_scoped_relations": scoped_registry.get("stable_relations", 0),
        "stable_sequential_constraints": sequential_registry.get("stable_relations", 0),
        "stable_numeric_constraints": numeric_registry.get("stable_relations", 0),
        "stable_discovered_relationships": relationship_registry.get("stable_relationships", 0),
        "unstable_discovered_relationships": relationship_registry.get("unstable_relationships", 0),
        "certified_for_configured_scope": bool(
            final_score == 0
            and not repairable_remaining
            and strong_stable
            and original_format_contract == table.format_contract()
            and (config.dry_run or (forward_replay_pass and inverse_roundtrip_pass))
        ),
    }

    report: dict[str, Any] = {
        "tool": "csv-consistency-repair",
        "version": __version__,
        "input": str(input_path),
        "output": str(output_path) if output_path else None,
        "config": asdict(config),
        "input_file_sha256": input_file_digest,
        "format_contract": {
            "input": original_format_contract,
            "output": table.format_contract(),
            "preservation_pass": original_format_contract == table.format_contract(),
            "strict_csv_parse": True,
        },
        "dialect": table.dialect.to_dict(),
        "input_logical_digest": initial_digest,
        "output_logical_digest": table.logical_digest(),
        "initial_score": initial_score,
        "final_score": final_score,
        "score_delta": final_score - initial_score,
        "strong_stable": strong_stable,
        "stable_streak": stable_streak,
        "final_status": final_status,
        "committed_edits": committed,
        "rejected_candidates": rejected_global,
        "cycles": [c.to_dict() for c in cycle_records],
        "relationship_discovery": relationship_registry,
        "numeric_constraint_discovery": numeric_registry,
        "temporal_constraint_discovery": temporal_registry,
        "scope_discovery": scoped_registry,
        "sequential_constraint_discovery": sequential_registry,
        "convergence_knowledge": public_knowledge_registry(knowledge_state),
        "constraint_graph": constraint_graph,
        "structural_repair": {
            "before": structural_before,
            "after": structural_after,
        },
        "global_repair_planning": {
            "features": {
                "global_minimum_edit_plan": True,
                "counterfactual_candidate_testing": True,
            },
            "cycles": global_plan_ledger,
        },
        "advanced_diagnostics": {
            "before": advanced_before,
            "after": advanced_after,
        },
        "maxima50": {"before": maxima_before, "after": maxima_after},
        "maxima55": {"before": maxima55_before, "after": maxima55_after},
        "closure": closure,
        "remaining_issues": [i.to_dict() for i in final_analysis.issues],
    }
    report["edit_explanations"] = explain_edits(committed, structural_after)
    report["unrepaired"] = next_evidence_from_issues(report["remaining_issues"])
    if config.dry_run:
        report["dry_run_plan"] = dry_run_plan(report)

    if output_path is not None and not config.dry_run:
        write_table(table, output_path)
        report["output_file_sha256"] = file_sha256(output_path)
    else:
        report["output_file_sha256"] = None
    if report_path is not None:
        report_path = Path(report_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return RepairResult(
        input_path=str(input_path),
        output_path=str(output_path) if output_path else None,
        report_path=str(report_path) if report_path else None,
        initial_score=initial_score,
        final_score=final_score,
        final_status=final_status,
        strong_stable=strong_stable,
        cycles=len(cycle_records),
        committed_edits=len(committed),
        remaining_issues=len(final_analysis.issues),
        input_logical_digest=initial_digest,
        output_logical_digest=table.logical_digest(),
        report=report,
    )


def undo(
    repaired_path: str | Path,
    report_path: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    repaired_path = Path(repaired_path)
    report_path = Path(report_path)
    output_path = Path(output_path)
    table = read_table(repaired_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    expected = report.get("output_logical_digest")
    if expected and table.logical_digest() != expected:
        raise ValueError("Repaired CSV does not match the report output digest.")

    edits = report.get("committed_edits", [])
    for edit in reversed(edits):
        if not _inverse_candidate(table, edit):
            raise ValueError(f"Could not reverse edit {edit.get('candidate_id')}")

    write_table(table, output_path)
    restored = table.logical_digest()
    original = report.get("input_logical_digest")
    return {
        "restored_path": str(output_path),
        "restored_logical_digest": restored,
        "expected_input_logical_digest": original,
        "logical_roundtrip_pass": bool(original and restored == original),
        "reversed_edits": len(edits),
    }
