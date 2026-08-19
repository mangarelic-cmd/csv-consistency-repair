# csv-consistency-repair 1.3.0 — benchmark repair pass

Date: 2026-08-19

## Result

Version 1.3.0 closes the four concrete failures found by the previous locked benchmark without weakening the conservative repair contract:

1. zero-configuration power is now essentially equal to configured power on the locked functional corpus;
2. cross-file proposals are materially committed and replayed;
3. sparse numeric corruption remains repairable through 30% on the tested relation instead of failing at 6%;
4. the streaming hot path is parallel, bounded-memory, and faster than the local Python/pandas baselines in non-replay throughput mode.

The deliberately ambiguous family remains unchanged by design: three cells are not repaired because the available table constraints do not identify which member of the single equation is wrong.

## Functional competition

Locked corpus: 36 datasets, 132 corrupted cells.

| Mode | Precision micro | Recall micro | Exact datasets | False mutations |
|---|---:|---:|---:|---:|
| configured 1.3.0 | 100.00% | 97.73% | 33/36 | 0 |
| zero-config 1.3.0 | 100.00% | 97.73% | 33/36 | 0 |
| prior 1.2.0 configured | 97.67% | 95.45% | 30/36 | 3 |
| prior 1.2.0 zero-config | — | 11.87% | — | — |

The remaining 3/132 unrepaired cells are the intentionally underdetermined cases. All other families in the locked corpus are recovered exactly.

## Format and safety

- Valid difficult CSVs: 160/160 logical roundtrips; 160/160 format-preservation passes; zero edits on all clean format cases.
- pandas logical parse in the same format harness: 103/160.
- Safety stress: 30/30 ambiguous/exception-bearing datasets left unchanged; 0 edits.
- Multi-file bundles: 5/5 exact after material commit, compared with 0/5 in 1.2.0.

## Sparse-corruption boundary

The locked numeric relation now shows:

| Corruption | Mean recall | Wrong clean rows |
|---:|---:|---:|
| 5% | 100% | 0 |
| 10% | 100% | 0 |
| 20% | 100% | 0 |
| 25% | 100% | 0 |
| 30% | 100% | 0 |
| 31% | 0% | 0 |
| 35% | 0% | 0 |
| 40% | 0% | 0 |


The tested boundary is therefore 30% → full recovery, 31% → abstention. This is intentionally conservative; no clean-row corruption was observed in these runs.

## Safe mode

| Rows | Wall inside repair | Exact recovery | Edits |
|---:|---:|---:|---:|
| 48 | 0.299s | yes | 1 |
| 400 | 1.787s | yes | 1 |
| 800 | 1.936s | yes | 1 |
| 2,000 | 2.278s | yes | 1 |


The old benchmark measured roughly 26.26s at 48 rows, 27.09s at 400 rows, and did not finish the 800-row run inside 70s. The current safe mode performs table-aware family pruning and no longer double-counts numeric repeated mappings as independent algebraic evidence.

## Streaming competition

Same transformation for every implementation: 2 boolean normalizations per row; outputs were byte-identical.

### 5 million rows

| Implementation | Wall | RSS max |
|---|---:|---:|
| 1.3.0 fast, no cold replay | 6.64s | 120.5 MB |
| Python csv baseline | 8.68s | 107.9 MB |
| pandas chunked | 15.38s | 1060.1 MB |
| 1.3.0 certified + cold replay | 13.50s | 120.9 MB |

### 10 million rows / 20 million material edits

| Implementation | Wall | RSS max |
|---|---:|---:|
| 1.3.0 fast, no cold replay | 14.10s | 120.6 MB |
| Python csv baseline | 17.87s | 107.9 MB |
| pandas chunked | 33.69s | 1209.3 MB |
| 1.3.0 certified + cold replay | 24.18s | 120.4 MB |

Fast mode is 1.27× faster than the specialized Python-csv baseline and 2.39× faster than pandas chunked on this workload. Certified mode performs a second independent transformation replay and still finishes in 24.18s. Compared with 1.2.0's 334.74s certified result, the 1.3.0 certified path is 13.8× faster.

## What changed technically

- table-aware automatic family selection for ordinary zero-config use;
- table-aware pruning inside safe mode;
- numeric discovery cache, safe early rejection of impossible candidate relations, and majority-consensus stability under sparse corruption;
- row-alignment reconstruction using exact affine witnesses only after unique positional alignment;
- analysis memoization and wide non-conflicting global-plan fast path;
- structural-evidence independence guard preventing a purely numeric repeated mapping from being counted as a second algebraic witness;
- bounded-memory binary fast path for simple quote-free CSVs with automatic fallback to the full parser;
- deterministic 4-worker ordered chunk path for large simple CSVs;
- inline input/output hashing and ordered chunk-Merkle replay receipts.

## Competition scope

This benchmark proves the numbers above against the local Python-csv, pandas and previously used sklearn-style baselines. It does not claim universal superiority over every commercial or academic data-repair product because those external products were not all installed and executed in this environment. The benchmark corpus, expected outputs, and current raw result JSONs are included so additional comparators can be added without changing the target answers.
