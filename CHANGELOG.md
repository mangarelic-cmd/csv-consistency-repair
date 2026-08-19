# Changelog

## 1.3.5

- Publication-readiness verification pass; no repair-algorithm authority was widened.
- Made all repository benchmark harnesses portable by removing development-machine absolute paths.
- Rebased the locked functional benchmark manifest onto repository-relative corpus paths while preserving every locked SHA-256.
- Packaged `AUTHOR.md` inside the installed Python package so the attribution referenced by the human/agent guides remains available after `pip install`.
- Removed duplicated attribution details from the README so `AUTHOR.md` is the single explicit project-reference note.
- Refreshed release metadata, Python 3.13 CI/classifier coverage, release checklist, citation metadata, source-manifest rules, and repository ignore rules.
- Removed stale absolute development paths from committed example reports.

## 1.3.4

- Added a cumulative convergence-knowledge registry across repair cycles.
- Relations now retain provenance and confidence/support trajectories instead of being forgotten after each rediscovery pass.
- Reports distinguish initial, revealed-after-repair, strengthened-after-repair, newly certified, and historical relations.
- Current candidate ordering may use cumulative relation knowledge as a hint, but historical knowledge never authorizes an edit by itself; all material changes still require current analyzer evidence plus the existing shadow/global-improvement gates.
- Added regression tests for weak-to-certified convergence, historical relation retention, and unchanged repair authority when knowledge memory is disabled.

## 1.3.3

- Verification pass: centralized runtime version metadata so reports match the installed package version.
- Undo now auto-detects bounded-memory streaming reports, while retaining explicit `--stream --undo` compatibility.
- Added regression tests for version/report consistency and automatic streaming undo dispatch.

## 1.3.2

- Adds `GUIDE_LLM.md`, a short operating guide for LLMs, agents, and automated tools.
- Adds `GUIDE_HUMAN.md`, a parallel quick-start guide for human users.
- Adds a concise guide index to the README so both usage paths are visible from package metadata and repository front pages.
- No repair-engine behavior changed from 1.3.1.

## 1.3.1

- Adds a public `AUTHOR.md` attribution note; project attribution details are kept there rather than duplicated across user-facing documentation.
- Adds an attribution pointer from the README.
- No repair-engine behavior changed from 1.3.0.

## 1.3.0

- Zero-configuration automatic repair now selectively enables high-confidence formula, relationship, scoped, temporal, sequential, and structural repair paths while preserving conservative abstention.
- Row-alignment repair can reconstruct a uniquely missing numeric field when an exact affine witness exists across the remaining table.
- Numeric relation discovery now tolerates sparse corruption up to a conservative majority boundary while still requiring independent reconstruction witnesses before editing.
- Safe mode was accelerated with memoized analysis, determinant-pair discovery, and report-only stratified diagnostics; repair decisions still use full data.
- Global planning tries the widest non-conflicting improving bundle before bounded combinatorial search.
- Streaming local repair adds a validated quote-free fast path and deterministic parallel chunk execution for large simple CSVs.
- Streaming input/output SHA-256 is computed inline, and parallel replay re-executes the transformation by ordered chunk digest.
- CLI adds `--no-auto` for users who want only explicitly requested operations.
- Cross-file materialization from 1.2.1 remains fully applied, remeasured, replayed, reversible, and atomic.

## 1.2.0

- Completes the current 130-item CSV MAXIMA backlog with the final 55 capabilities, including semantic identity/schema reconciliation, transactional multi-file safety, streaming/scale utilities, user-facing explanations and abstention reasons, and a locked ground-truth benchmark interface.
- Adds `--safe` one-command conservative mode.
- Adds frozen multi-file snapshots, precommit TOCTOU revalidation, staged canary execution, atomic bundle commit, rollback journals, dependent-file revalidation, checkpoints/resume metadata, and bundle-wide undo.
- Adds bounded-memory `stream_scan`, deterministic sampling/shard plans, sparse graph export, parallel deterministic diagnostic helpers, mmap/SQLite line indexes, optional NumPy vectorized kernels, and parallel join indexes.
- Adds bounded-memory local stream repair with deterministic cold replay and JSONL-backed streaming undo, including exact-dedup restoration.
- Adds semantic header/category diagnostics, polysemy guards, entity/version lineage, automatic foreign-key candidates, rule precedence, source lineage/trust/freshness metadata, and non-double-counting/cascade ledgers.
- Adds per-edit explanations/correctability, unrepaired-reason taxonomy, suggested evidence, dry-run projected plans, learned-rule export, constraint-drift reports, and stdin/stdout pipeline support.
- Adds `csv-consistency-benchmark` with locked corpus hashing, precision/recall/false-mutation/exact-recovery metrics, zero-config/configured comparison, clean-file and corruption-family reporting, optional pandas baseline, and pluggable comparator outputs.
- Keeps the default package dependency-free; vectorized acceleration is optional when NumPy is already available.

## 1.1.0

- Added 50 advanced CSV repair/diagnostic capabilities spanning structural recovery, canonicalization, missingness/uncertainty, richer relationship discovery, structural reconstruction, multi-file evidence, and validation guards.
- Added opt-in conservative repairs for unique row realignment, unambiguous locale numbers, safe header canonicalization, and multi-witness rank-1 missing values.
- Added bundle-level automatic relationship and cross-file reconstruction proposals.
- Fixed bounded-plan starvation discovered during the verification pass by allowing a larger mutually compatible shadow-certified batch to override a smaller plan when it closes strictly more of the repair objective.
- Added PASS013/PASS014 regression, black-box, diagnostic fuzz, format fuzz, deterministic build, and wheel-integrity checks.

## 0.9.0

- Adds a per-cell constraint-syndrome ledger showing which violated invariants implicate each cell.
- Adds bounded exact minimum hitting-set localization, with deterministic greedy fallback on larger constraint sets.
- Adds witness-independence accounting so repeated reports from the same detector/relation do not count as multiple independent witnesses.
- Adds cell-level correctability classes: unique multi-witness, single-witness, conflicting reconstruction, and localized-without-reconstruction.
- Adds a deterministic fault-isolation ranking based on minimum-explanation membership, severity, violation support, and independent evidence.
- Adds conservative cross-analyzer consensus repair when distinct analyzer families and distinct relation witnesses reconstruct exactly the same value; conflicting reconstructions abstain.
- Preserves forward replay, inverse roundtrip, format preservation, and two-cycle stability requirements.

## 0.8.0

- Adds scope-aware affine formula discovery for categorical groups.
- Adds conservative row-segment change-point formula discovery.
- Adds learned source-range guards so automatic projection does not extrapolate outside observed formula scope.
- Adds running-balance discovery across adjacent rows.
- Adds two-sided sequence reconstruction: existing balance values are repaired only when forward and backward checks agree.
- Adds conservative missing-value projection from exact stable sequential relations.
- Extends the constraint graph with scoped formulas, row-segment formulas, and running-balance relations.
- Adds installed-wheel black-box tests for scope-specific formulas, row-segment transitions, sequential repair, out-of-domain abstention, and random-table no-mutation.

## 0.7.0

- Adds conservative reconstruction of missing cells from stable table structure.
- Adds inverse formula projection when exactly one member of a stable equation is missing.
- Extends automatic numeric discovery to common three-source formulas such as `a*b+c`, `a*b-c`, `a+b+c`, and `a+b-c`.
- Adds two-column determinant discovery for conditional/composite repeated mappings.
- Adds exact start/end/duration relationship discovery and missing temporal projection.
- Adds a machine-readable constraint graph connecting stable mappings and formulas.
- Uses a repair-first lexicographic objective so a safe projection is not rejected merely because it reveals a separate non-repairable issue such as an exact duplicate row.
- Adds installed-wheel black-box tests for reconstructible blanks, random abstention, and underdetermined abstention.

## 0.6.0

- Adds automatic numeric-equation discovery across CSV columns.
- Adds boundary-stress checks across row partitions and tighter numerical tolerances.
- Adds redundant-constraint consensus: an automatic edit is allowed only when multiple independent stable equations reconstruct the same cell value.
- Keeps one-equation anomalies diagnostic-only to avoid guessing which operand is wrong.
- Adds machine-readable numeric constraint registry and closure receipts.

## 0.5.0

- External black-box validation found and reproduced a version-0.4 delimiter-detection failure on semicolon files containing quoted/multiline content.
- Replaced delimiter selection with structural parsing/scoring across comma, semicolon, tab, and pipe candidates.
- Stopped trusting `csv.Sniffer` for quote mechanics; output uses standard double-quote CSV escaping, preventing `doublequote=False` write failures on embedded quotes.
- Added regression tests for semicolon, tab, and pipe inputs with embedded delimiter text, quotes, multiline cells, and outer whitespace.
- Added external installed-wheel ground-truth validation and adversarial no-mutation/exact-reconstruction fuzzing.
- Retains the UTF-8/BOM, strict parsing, reversible edit log, forward replay, inverse roundtrip, configuration validation, rule-schema validation, and public API hardening introduced during the 0.4 release work.

## 0.3.0

- Added stability-gated discovery of repeated single-column mappings.
- Added row-scope, threshold, normalization, and missing-value stress checks.
- Added deterministic forward replay and inverse roundtrip verification.

## 0.2.0

- Added declared functional dependencies, formulas, units, unique keys, foreign keys, allowed values, ranges, and multi-file bundles.

## 0.1.0

- Initial reversible CSV consistency repair engine.
