# csv-consistency-repair

Conservative automatic consistency repair for messy CSV datasets.

The package detects ordinary table defects, proposes minimal reversible edits, checks candidate edits against the whole table, applies only edits that improve the measured consistency score, and writes a machine-readable repair report.

Version 1.3 turns the completed feature sweep into a much faster automatic repair path: ordinary zero-configuration use now selects high-confidence repair families automatically, sparse numeric corruption is handled by majority-stable constraint discovery, safe mode prunes irrelevant analyzers by table type, and large simple CSVs use a deterministic parallel bounded-memory stream path. Version 1.1 extended the structural fault-localization core with bounded global repair planning, counterfactual shadow testing, common-cause diagnostics, redundancy scoring, ambiguity-preserving candidate sets, and regime/statistical diagnostics while preserving the same conservative commit, replay, and roundtrip rules. It records which constraints implicate each cell, ranks likely fault locations, estimates whether a cell is uniquely reconstructible or ambiguous, avoids double-counting repeated evidence, and can synthesize a repair when distinct analyzer families independently reconstruct the same value. It preserves the detected CSV dialect and UTF-8 BOM, rejects malformed/non-UTF-8 input explicitly, validates configuration and rule schemas before repair, records file hashes and format-preservation receipts, and keeps forward replay plus inverse roundtrip verification as closure requirements.

The hardened parser also replaces generic CSV delimiter/quote sniffing with structural delimiter selection across comma, semicolon, tab, and pipe inputs, and uses standard double-quote CSV escaping. This change was made after an external black-box test found that version 0.4 could misclassify some semicolon files containing quoted/multiline content.






## Zero-configuration automatic repair

The ordinary `repair()` call now profiles the table cheaply and enables only the repair families that have enough structural evidence to be useful. It can automatically recover stable numeric formulas, missing values, repeated mappings, group-scoped rules, temporal/sequential relations, safe row alignment, locale numerics, and low-rank missing cells. Ambiguous cases are left unchanged.

```python
from csv_consistency_repair import repair
repair("dirty.csv", "clean.csv", "repair.json")
```

Use `RepairConfig(auto_mode=False)` or CLI `--no-auto` when you want the historical explicit-only behavior. For large quote-free local-cleanup workloads, `--stream` uses bounded memory; the streaming API can also enable deterministic parallel workers and optional cold replay.

## Safe mode and final feature layer

Version 1.2 adds a single conservative entry point for the broad feature set:

```bash
csv-consistency-repair input.csv -o fixed.csv --report repair.json --safe
```

`--safe` enables broad relationship, numeric, temporal, scoped, sequential, structural, and advanced diagnostics while material edits remain subject to the same whole-table shadow test, minimum-edit objective, forward replay, inverse roundtrip, and ambiguity abstention rules.

The release also adds ordinary data-quality capabilities for:

- semantic header alignment, category/abbreviation candidates, typo proposals, polysemy guards, entity/version identity, and reversible schema-migration suggestions;
- frozen multi-file snapshots, automatic foreign-key candidates, source lineage, source freshness/trust metadata, shadow bundle staging, TOCTOU checks, atomic commit, rollback journals, dependency revalidation, checkpoints, and bundle-wide undo;
- bounded-memory streaming statistics, deterministic sampling, sparse constraint-graph export, parallel diagnostic helpers, out-of-core mmap/SQLite indexes, chunk-boundary sequential state, optional NumPy vectorized kernels, and parallel join indexes;
- per-edit witness explanations, correctability classes, machine-readable unrepaired reasons, suggested next evidence, learned-rule export, and constraint-drift comparison;
- a locked ground-truth benchmark harness reporting precision, recall, false-mutation rate, exact-dataset recovery, abstention, throughput, memory, corruption family, zero-config/configured results, and pluggable comparator outputs.

Bounded-memory scan and fast local-repair path from Python:

```python
from csv_consistency_repair import stream_scan, stream_repair, StreamRepairConfig

stats = stream_scan("large.csv")
stream_repair(
    "large.csv", "large.fixed.csv", "large.report.json",
    StreamRepairConfig(normalize_booleans=True),
)
```

The streaming repair path is deliberately limited to local exact operations (outer whitespace, optional null/boolean normalization, optional exact deduplication). Global formula/relationship discovery uses the full repair engine. Streaming repairs can be reversed from their JSONL journal without loading the full dataset into memory:

```python
from csv_consistency_repair import stream_undo
stream_undo("large.fixed.csv", "large.report.json", "large.restored.csv")
```

The same operation is available from the CLI with `--stream --undo`.

Pipeline mode uses the conventional `-` paths; add `--stream` for the bounded-memory local-repair path:

```bash
cat input.csv | csv-consistency-repair - -o - --report repair.json --stream > fixed.csv
```

Learned constraints can be frozen for later reuse/audit:

```bash
csv-consistency-repair input.csv -o fixed.csv --report repair.json \
  --safe --export-rules learned-rules.json
```

Multi-file bundles are staged against one frozen snapshot and are not committed until canary/replay checks pass and the inputs are re-hashed immediately before commit. A transaction journal and backups permit deterministic rollback.

The benchmark harness is separate from ordinary repair so benchmark inputs can be locked before evaluation:

```bash
csv-consistency-benchmark benchmark-manifest.json \
  --lock benchmark-lock.json --output benchmark-results.json
```

To reproduce the repository benchmark comparators, install the optional benchmark dependencies:

```bash
pip install -e '.[benchmark]'
```

Repository benchmark scripts use paths relative to the checkout and do not depend on the original development machine.

Declaring a corpus `external` in the benchmark manifest does not itself prove independence; the report only allows that label when provenance is supplied and all referenced files are locked by hash.

## Advanced reconstruction and 50-feature diagnostics (1.1)

Version 1.1 adds an opt-in advanced diagnostics layer for difficult CSV repair cases while keeping the normal repair surface conservative. It covers structural row recovery, header repair, locale-aware numbers, date/time inference, reversible units, near-duplicate residue tracking, structured missingness and censoring, higher-arity relationships, conditional and aggregate rules, lag/seasonal diagnostics, multi-rate data, low-rank missing-cell reconstruction, sparse-corruption localization, state-space/interpolation candidates, cross-file evidence, and independent validation guards.

Enable diagnostics without granting any new edit authority:

```bash
csv-consistency-repair input.csv -o fixed.csv --report repair.json --maxima
```

A small subset of the new capabilities can be allowed to materialize edits when their strict safety gates pass:

```bash
csv-consistency-repair input.csv -o fixed.csv --report repair.json \
  --maxima-repair \
  --maxima-repair-headers \
  --maxima-repair-row-alignment \
  --maxima-repair-locale-numbers \
  --maxima-repair-low-rank-missing
```

Low-rank missing-cell projection is accepted only when multiple independent 2x2 witnesses reconstruct the same value. Locale normalization requires a decisive column-wide interpretation. Short/long row realignment requires a unique type-compatible alignment. Ambiguous cases remain unchanged.

Version 1.1 also fixes a verification-discovered starvation case in the global planner: when a bounded small repair bundle improves the table but a larger mutually compatible batch closes strictly more of the global objective, the larger shadow-certified batch is preferred.

## Global planning and advanced diagnostics

Version 1.0 evaluates already-proposed repair candidates in shadow state before material commit. For bounded candidate sets it can select the smallest jointly improving bundle instead of relying only on greedy one-cell edits. The report keeps the singleton counterfactual results and the selected bundle under `global_repair_planning`. Candidate generation remains conservative: this planner never invents new values.

The same release adds ordinary data-quality diagnostics for common failure modes: contiguous/burst corruption, shared scale shifts, change points, drifting windows, recurring regimes, saturation, calibration shifts, hysteresis/multistability warnings, residual autocorrelation, bootstrap stability, k-fold validation, and complexity-aware relation ranking. These diagnostics are used as evidence and abstention guards; they do not authorize a repair by themselves.

Ambiguous cases remain explicit. Reports can preserve multiple legal candidate values and include `suggested_next_evidence` describing what extra independent relation, reference file, or source column would make a repair identifiable.

## Structural fault localization

Version 0.9 adds a `structural_repair` section to every report. Before and after repair it records a per-cell constraint syndrome, an exact minimum-cardinality explanation when the problem is small enough (with deterministic greedy fallback for larger cases), witness-independence counts, correctability classes, and a fault-isolation ranking.

The repairer can also fuse independent evidence conservatively: if two distinct analyzer families and two distinct relation witnesses reconstruct the same cell value, a reversible consensus candidate is created. If the witnesses disagree, the cell is reported as `CONFLICTING_RECONSTRUCTIONS` and is left unchanged.

Typical report fields include:

- `minimum_explanation_cells`;
- `fault_isolation_ranking`;
- `independent_witness_count`;
- `correctability`;
- `reliability_score`.

This remains ordinary CSV repair behavior: the extra structure is used to identify which cells best explain a set of consistency failures and when the available evidence is strong enough to repair rather than guess.

## Redundant constraint consensus

Version 0.6 can infer simple numeric equations directly from repeated table structure. With `discover_numeric_constraints=True`, it searches stable `sum`, `difference`, `product`, and `ratio` relations. With `repair_numeric_constraints=True`, it still does not repair from a single inferred equation: at least two independently discovered stable equations must reconstruct the same cell value. This makes redundant data structure act as its own repair witness.

```python
cfg = RepairConfig(
    discover_numeric_constraints=True,
    repair_numeric_constraints=True,
)
repair("ledger.csv", "ledger.fixed.csv", config=cfg)
```

For example, if a table repeatedly satisfies both `subtotal = qty * price` and `subtotal = total - tax`, an isolated bad `subtotal` can be reconstructed without a declared formula. If only one equation supports a change, the anomaly is reported but not automatically rewritten.


## Structural missing-value projection

Version 0.7 can reconstruct some missing cells directly from stable table structure. The feature is conservative: it only fills a blank when the surrounding data provides a stable, uniquely solvable relation.

The CLI convenience switch enables mapping, numeric-formula, and elapsed-time projection together:

```bash
csv-consistency-repair input.csv -o fixed.csv --report repair.json --project-missing
```

Supported automatic projection paths include:

- exact repeated mappings such as `(region, code) -> label`;
- two-column formulas such as `subtotal = qty * price`;
- common three-source formulas such as `total = qty * price + shipping`;
- inverse formula projection when exactly one member is missing;
- exact elapsed-time relations such as `end = start + duration`.

A formula can be solved in either direction when exactly one member is blank. Two missing members are treated as underdetermined and are left untouched. Single-formula projection is accepted only when the learned relation is exact across all eligible evidence; otherwise multiple compatible relations are required.

Python example:

```python
from csv_consistency_repair import RepairConfig, repair

cfg = RepairConfig(
    discover_numeric_constraints=True,
    numeric_max_formula_terms=3,
    repair_missing_values=True,
    discover_relationships=True,
    discovery_max_determinant_columns=2,
    discover_temporal_constraints=True,
    repair_temporal_missing=True,
)
repair("input.csv", "fixed.csv", "repair.json", cfg)
```

The report includes a `constraint_graph` showing which stable mappings and formulas connect each column and which cells are supported by multiple independent relations.


## Scope-aware and sequential repair

Version 0.8 can learn that a formula is valid only inside a specific row group or row segment instead of forcing one global rule over the entire file. This is useful when the same columns follow different formulas for different product types, countries, statuses, devices, currencies, or time periods. Learned formulas are stress-tested inside their own scope and are not used outside the source range observed for that scope.

```python
cfg = RepairConfig(
    discover_scoped_relations=True,
    repair_missing_values=True,
    repair_scoped_missing=True,
)
repair("input.csv", "fixed.csv", "repair.json", cfg)
```

The package can also discover ordinary running-balance relationships across adjacent rows, such as `balance[t] = balance[t-1] + credit[t] - debit[t]`. Missing sequence values can be computed from exact stable relations, while an existing balance is only rewritten when forward and backward checks independently reconstruct the same value.

```python
cfg = RepairConfig(
    discover_sequential_constraints=True,
    repair_missing_values=True,
    repair_sequential_missing=True,
    repair_sequential_values=True,
)
```

The report records these findings under `scope_discovery`, `sequential_constraint_discovery`, and the ordinary `constraint_graph`. Values outside the learned range remain untouched rather than being extrapolated automatically.


## Quick guides

- Automated tools, LLMs, and agents: [`GUIDE_LLM.md`](GUIDE_LLM.md)
- Human quick start: [`GUIDE_HUMAN.md`](GUIDE_HUMAN.md)
- Project attribution: [`AUTHOR.md`](AUTHOR.md)

For a normal repair, keep the original file and run:

```bash
csv-consistency-repair input.csv -o repaired.csv --report repair.json
```

Use `--safe` for broader conservative structural repair. Use `--stream` only for bounded-memory local cleanup of very large files.

## Install

```bash
pip install csv-consistency-repair
```

## Basic Python API

```python
from csv_consistency_repair import repair

result = repair(
    "input.csv",
    output_path="fixed.csv",
    report_path="repair_report.json",
)

print(result.final_score)
print(result.final_status)
```

## CLI

```bash
csv-consistency-repair input.csv -o fixed.csv --report repair_report.json
```

Optional conservative repair policies:

```bash
csv-consistency-repair input.csv -o fixed.csv --report repair_report.json \
  --deduplicate --normalize-null-markers --normalize-booleans
```

Undo a completed repair using the reversible report:

```bash
csv-consistency-repair fixed.csv --undo --report repair_report.json -o restored.csv
```



## Input and format contract

The current input contract is UTF-8 CSV. UTF-8 BOM files are supported and the BOM is preserved in repaired and restored output. The detected delimiter, quote settings, and line terminator are also preserved. Supported delimiter detection covers comma, semicolon, tab, and pipe. Malformed CSV is rejected rather than silently reinterpreted.

Reports include `input_file_sha256`, `output_file_sha256`, and a `format_contract` receipt. A clean consistency score is not promoted to configured-scope closure if format preservation, forward replay, inverse roundtrip, or the required stable cycles fail.

Large fields are supported beyond Python's default CSV parser field-size limit. CSV cells are always treated as data: strings such as `=SUM(A1:A2)` are never executed.

## Public API

The initial public API frozen for the 0.4 release line is:

```python
from csv_consistency_repair import (
    RepairConfig,
    RepairResult,
    repair,
    undo,
    repair_bundle,
    undo_bundle,
    stream_scan,
    export_learned_rules,
    drift_report,
    run_benchmark,
    lock_benchmark_protocol,
)
```

All other modules should be considered implementation details unless documented here.

## Stability-gated relationship discovery

Relationship discovery is opt-in:

```bash
csv-consistency-repair input.csv -o fixed.csv --report repair.json \
  --discover-relationships
```

To allow automatic correction from a discovered repeated mapping:

```bash
csv-consistency-repair input.csv -o fixed.csv --report repair.json \
  --repair-discovered-relationships
```

A discovered mapping is not trusted merely because it fits the complete file. Before it can repair data, the package checks that the mapping:

- has enough repeated-row evidence;
- exceeds the configured confidence threshold;
- remains consistent across even/odd and first/second row scopes when those scopes contain enough evidence;
- still passes a stricter confidence probe;
- is not materially changed by trim/case normalization;
- is not dominated by missing-value scope effects.

Mappings that fail these checks remain diagnostic only. The report records the exact failed stability checks instead of selecting the row range or threshold that gives the best result.

Python example:

```python
from csv_consistency_repair import RepairConfig, repair

result = repair(
    "input.csv",
    "fixed.csv",
    "repair.json",
    RepairConfig(
        discover_relationships=True,
        repair_discovered_relationships=True,
        discovery_confidence=0.95,
    ),
)
```

The report also includes `unique_key_candidates` as observations. They are not used to delete or merge records automatically.

## Replay and roundtrip verification

Every committed non-dry repair is checked in both directions before the final status is issued:

1. the edit log is replayed from the original logical table and must reconstruct the repaired logical table exactly;
2. the committed edits are reversed from the repaired logical table and must reconstruct the original logical table exactly.

The machine-readable receipt is stored under `closure.forward_replay_pass` and `closure.inverse_roundtrip_pass`. A replay failure overrides a clean score and produces `REPLAY_FAILED`.

## Declared relationship rules

Rules are ordinary JSON. They make semantic repairs explicit rather than guessed.

Example `orders.rules.json`:

```json
{
  "unique": ["order_id"],
  "functional_dependencies": [
    {
      "determinant": ["customer_id"],
      "dependent": "country",
      "min_support": 3,
      "min_confidence": 0.75,
      "repair": true
    }
  ],
  "row_formulas": [
    {
      "target": "total",
      "expression": {
        "op": "sum",
        "columns": ["subtotal", "tax"]
      },
      "tolerance": 0.01,
      "repair": true
    }
  ],
  "units": [
    {
      "column": "length",
      "canonical": "m",
      "aliases": {"cm": 0.01, "mm": 0.001},
      "repair": true
    }
  ],
  "foreign_keys": [
    {
      "column": "customer_id",
      "reference_file": "customers.csv",
      "reference_column": "customer_id"
    }
  ],
  "allowed_values": [
    {"column": "status", "values": ["open", "closed", "cancelled"]}
  ],
  "ranges": [
    {"column": "discount_percent", "min": 0, "max": 100}
  ]
}
```

Run it with:

```bash
csv-consistency-repair orders.csv -o orders.fixed.csv \
  --report orders.repair.json --rules orders.rules.json
```

The rules file is resolved relative to its own directory, so foreign-key reference files can use simple relative paths.

## Supported declared rules

### Unique keys

```json
{"unique": ["id", ["store_id", "sku"]]}
```

Violations are reported. Conflicting records are not deleted automatically.

### Functional dependencies

```json
{
  "functional_dependencies": [
    {
      "determinant": ["customer_id"],
      "dependent": "country",
      "min_support": 3,
      "min_confidence": 0.8,
      "repair": true
    }
  ]
}
```

If a repeated determinant has one unambiguous dominant dependent value, a declared repair may correct an outlier. The proposed cell edit is still re-tested against the full table before commit.

### Row formulas

Supported operations are `sum`, `product`, `difference`, and `ratio`.

```json
{
  "row_formulas": [
    {
      "target": "total",
      "expression": {"op": "sum", "columns": ["subtotal", "tax"]},
      "tolerance": 0.01,
      "repair": true
    }
  ]
}
```

### Unit normalization

```json
{
  "units": [
    {
      "column": "length",
      "canonical": "m",
      "aliases": {"cm": 0.01, "mm": 0.001},
      "repair": true
    }
  ]
}
```

The package only converts units explicitly declared in the rule.

### Foreign keys

```json
{
  "foreign_keys": [
    {
      "column": "customer_id",
      "reference_file": "customers.csv",
      "reference_column": "customer_id"
    }
  ]
}
```

Orphan values are reported but not guessed or replaced.

### Allowed values and ranges

```json
{
  "allowed_values": [
    {"column": "state", "values": ["OK", "WARN", "FAIL"]}
  ],
  "ranges": [
    {"column": "temperature", "min": -50, "max": 150}
  ]
}
```

These constraints are diagnostic unless a deterministic repair is separately defined.

## Multi-file bundles

A bundle manifest can run several CSV repairs as one job:

```json
{
  "datasets": [
    {
      "name": "customers",
      "input": "customers.csv",
      "rules": "customers.rules.json"
    },
    {
      "name": "orders",
      "input": "orders.csv",
      "rules": "orders.rules.json",
      "config": {"normalize_booleans": true}
    }
  ]
}
```

Python:

```python
from csv_consistency_repair import repair_bundle

summary = repair_bundle(
    "bundle.json",
    output_dir="repaired",
    report_path="bundle.report.json",
)
```

CLI:

```bash
csv-consistency-repair bundle.json --bundle \
  -o repaired --report bundle.report.json
```

## Default behavior

Without a rules file, the package automatically applies only low-risk formatting repairs:

- leading/trailing whitespace in cells;
- leading/trailing whitespace in column names when it does not create a collision.

It also reports, without silently changing them:

- exact duplicate rows;
- inconsistent column widths;
- duplicate column names;
- dominant-type violations;
- common null-marker mixtures;
- mixed boolean spellings.

Potentially destructive or semantic changes require explicit policy flags or declared rules.

## Repair model

Every cycle follows the same table-native contract:

1. all analyzers inspect the same frozen table snapshot independently;
2. their findings are combined and conflicts are retained rather than hidden;
3. a candidate edit is tested on a copy of the full table;
4. the complete table is re-analyzed after the candidate edit;
5. the edit is committed only when the global consistency score decreases;
6. a two-step composition may be tested atomically when the first safe edit alone does not improve the global score;
7. the process repeats until two consecutive stable cycles or the configured cycle limit;
8. every committed edit is recorded with enough information for logical-table reversal.

The report distinguishes detected issues, candidate edits, committed edits, rejected edits, remaining issues, score history, and stabilization state.

## Safety

The package does not guess missing business rules. If a value is suspicious but there is no deterministic correction supported by the input table and declared rules, it remains in the report instead of being rewritten.


## Convergence knowledge

Each repair cycle still re-checks the current table from scratch, but the engine also keeps a cumulative registry of what it has learned across cycles. A relation can therefore be tracked as weak, strengthened after a repair, newly certified, revalidated, or historical. The report exposes this under `convergence_knowledge`, including provenance and confidence/support history.

This memory is deliberately non-authoritative: a historical relation cannot repair data by itself. It may only help order and explain candidates that are independently proposed from the current table; every edit must still pass the normal shadow validation, objective-improvement, replay, and roundtrip checks.

## Attribution

For project attribution, see [`AUTHOR.md`](AUTHOR.md).
