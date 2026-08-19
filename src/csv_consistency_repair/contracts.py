from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .engine import RepairConfig


def validate_config(config: "RepairConfig") -> "RepairConfig":
    if config.safe_mode:
        # Broad discovery, conservative repair.  Every enabled edit surface already
        # passes the engine's shadow/global/replay/roundtrip contract.
        config = replace(
            config,
            discover_relationships=True, repair_discovered_relationships=True, discovery_max_determinant_columns=2,
            discover_numeric_constraints=True, repair_numeric_constraints=True, repair_missing_values=True,
            discover_temporal_constraints=True, repair_temporal_missing=True,
            discover_scoped_relations=True, repair_scoped_missing=True, repair_scoped_values=True,
            discover_sequential_constraints=True, repair_sequential_missing=True, repair_sequential_values=True,
            structural_consensus=True, global_repair_plan=True, advanced_diagnostics=True,
            maxima50=True, maxima55=True, maxima_repair=True,
            maxima_repair_headers=True, maxima_repair_row_alignment=True,
            maxima_repair_locale_numbers=True, maxima_repair_low_rank_missing=True,
        )
    if config.max_cycles < 1:
        raise ValueError("max_cycles must be at least 1.")
    if config.stable_cycles_required < 1:
        raise ValueError("stable_cycles_required must be at least 1.")
    if config.stable_cycles_required > config.max_cycles:
        raise ValueError("stable_cycles_required cannot exceed max_cycles.")
    if config.discovery_min_rows < 4:
        raise ValueError("discovery_min_rows must be at least 4.")
    if config.discovery_min_group_support < 2:
        raise ValueError("discovery_min_group_support must be at least 2.")
    if not 0.5 <= config.discovery_confidence <= 1.0:
        raise ValueError("discovery_confidence must be between 0.5 and 1.0.")
    if not 0.0 <= config.discovery_min_coverage <= 1.0:
        raise ValueError("discovery_min_coverage must be between 0 and 1.")
    if not 0.0 <= config.discovery_stress_tolerance <= 0.5:
        raise ValueError("discovery_stress_tolerance must be between 0 and 0.5.")
    if config.numeric_min_independent_constraints < 2:
        raise ValueError("numeric_min_independent_constraints must be at least 2.")
    if config.numeric_abs_tolerance < 0 or config.numeric_rel_tolerance < 0:
        raise ValueError("numeric tolerances must be nonnegative.")
    if config.numeric_max_columns < 3:
        raise ValueError("numeric_max_columns must be at least 3.")
    if config.numeric_max_formula_terms not in (2, 3):
        raise ValueError("numeric_max_formula_terms must be 2 or 3.")
    if config.numeric_missing_min_constraints < 1:
        raise ValueError("numeric_missing_min_constraints must be at least 1.")
    if config.discovery_max_determinant_columns not in (1, 2):
        raise ValueError("discovery_max_determinant_columns must be 1 or 2.")
    if config.temporal_max_columns < 3:
        raise ValueError("temporal_max_columns must be at least 3.")
    if config.scope_min_rows < 8:
        raise ValueError("scope_min_rows must be at least 8.")
    if not 0.9 <= config.scope_confidence <= 1.0:
        raise ValueError("scope_confidence must be between 0.9 and 1.0.")
    if config.scope_max_groups < 2:
        raise ValueError("scope_max_groups must be at least 2.")
    if config.structural_consensus_min_families < 2:
        raise ValueError("structural_consensus_min_families must be at least 2.")
    if config.global_plan_max_candidates < 2:
        raise ValueError("global_plan_max_candidates must be at least 2.")
    if config.global_plan_max_bundle < 2:
        raise ValueError("global_plan_max_bundle must be at least 2.")
    if config.global_plan_max_bundle > config.global_plan_max_candidates:
        raise ValueError("global_plan_max_bundle cannot exceed global_plan_max_candidates.")
    if config.global_plan_max_trials < 1:
        raise ValueError("global_plan_max_trials must be at least 1.")
    if config.sequential_min_rows < 10:
        raise ValueError("sequential_min_rows must be at least 10.")
    if not 0.9 <= config.sequential_confidence <= 1.0:
        raise ValueError("sequential_confidence must be between 0.9 and 1.0.")
    if (config.repair_numeric_constraints or config.repair_missing_values) and not config.discover_numeric_constraints:
        # Missing-value projection can use numeric constraints when available; relationship discovery remains separately opt-in.
        if config.repair_numeric_constraints:
            config = replace(config, discover_numeric_constraints=True)
    if config.repair_temporal_missing and not config.discover_temporal_constraints:
        config = replace(config, discover_temporal_constraints=True)
    if (config.repair_scoped_missing or config.repair_scoped_values) and not config.discover_scoped_relations:
        config = replace(config, discover_scoped_relations=True)
    if (config.repair_sequential_missing or config.repair_sequential_values) and not config.discover_sequential_constraints:
        config = replace(config, discover_sequential_constraints=True)
    if config.maxima_expression_terms < 3 or config.maxima_expression_terms > 5:
        raise ValueError("maxima_expression_terms must be between 3 and 5.")
    if config.maxima_repair and not config.maxima50:
        config = replace(config, maxima50=True)
    if config.rules_path is not None and not Path(config.rules_path).is_file():
        raise ValueError(f"Rules file does not exist: {config.rules_path}")
    if config.repair_discovered_relationships and not config.discover_relationships:
        return replace(config, discover_relationships=True)
    return config
