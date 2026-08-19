# Verification — 1.3.5

Date: 2026-08-19

This release-candidate pass was performed after the cumulative-knowledge change and after publication-hygiene fixes. It did not widen repair authority.

## Regression and package integrity

- Source suite: **109/109 passed**.
- Installed-wheel suite: **109/109 passed**.
- Python compilation of package, tests, benchmark harnesses, and benchmark scripts: passed.
- Wheel built twice with the same fixed build epoch: **byte-identical**.
- Wheel `RECORD`: **34 hashed entries checked, 0 failures**.
- Installed package metadata: name `csv-consistency-repair`, version `1.3.5`, Python `>=3.10`, MIT.
- Human guide, LLM/agent guide, and `AUTHOR.md`: present in the installed wheel.
- The wheel/PyPI README metadata contains only an attribution pointer; the explicit project reference remains in `AUTHOR.md`.
- Public benchmark scripts and committed example reports contain no development-machine absolute paths.
- Locked functional benchmark paths are repository-relative and every corpus SHA-256 is revalidated by the test suite.
- Secret/private-key pattern scan: 0 hits.
- GitHub Actions release matrix: **Python 3.10, 3.11, 3.12, and 3.13 all passed tests and package build**.
- The first public CI matrix exposed a Python 3.10-only test dependency issue (`tomllib`); it was corrected with a `tomli` compatibility fallback and the full matrix was rerun green before release. Runtime repair code was unchanged by this fix.

## Idempotence

The automatic repair output was run through four consecutive clean passes. The first zero is treated as warm-up and is **not counted**.

- Warm-up zero: 0 edits, byte-identical, forward replay passed, inverse roundtrip passed.
- Counted zero 1: 0 edits, byte-identical, forward replay passed, inverse roundtrip passed.
- Counted zero 2: 0 edits, byte-identical, forward replay passed, inverse roundtrip passed.
- Counted zero 3: 0 edits, byte-identical, forward replay passed, inverse roundtrip passed.

Additional stability checks:

- Safe mode: repaired once, then two zero-edit byte-identical repeats.
- Streaming: second pass produced 0 edits; undo restored the original bytes exactly.
- Multi-file bundle: first run committed 15 edits atomically; second run produced 0 edits and remained exact.

## Automatic/function benchmarks replayed

Locked functional corpus, 36 datasets / 132 corrupted cells:

- precision micro: **100%**;
- recall micro: **97.727%**;
- exact datasets: **33/36**;
- false mutations: **0**;
- zero-configuration result: same precision/recall/exact-recovery totals.

The three non-exact datasets are the intentionally ambiguous cases and remain unchanged rather than guessed.

Other replayed checks:

- difficult-format corpus: **160/160** parsed and logical-roundtripped, **160/160** format-preservation checks passed, 0 edits on clean files;
- safety stress: **30/30** files unchanged, 0 mutations;
- multi-file bundle: **5/5** exact, transaction state `COMMITTED` in every case;
- corruption-boundary test: 100% recovery with 0 clean-row mutations at 5%, 10%, 20%, 25%, and 30%; conservative abstention begins at 31% in this test;
- safe-mode scaling: 48, 400, 800, and 2,000-row cases all exact with replay/roundtrip passed;
- portable generated streaming check: 1,000,000 rows, 2,000,000 edits, replay passed.

## Publication status

The verified source tree is deployed to the public GitHub repository at https://github.com/mangarelic-cmd/csv-consistency-repair and the GitHub Actions Python 3.10–3.13 matrix is green. The release files are technically ready for a GitHub tag/release and a Zenodo software deposit. The wheel is technically ready for PyPI upload. External account-side actions not yet claimed as verified are PyPI package-name availability and Trusted Publisher configuration, the GitHub release/tag action, and the Zenodo deposit itself. An independent third-party real-world benchmark also remains open and is not represented as completed.
