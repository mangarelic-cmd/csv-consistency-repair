# Quick Guide for Humans

`csv-consistency-repair` repairs CSV files conservatively. It can clean ordinary defects, recover some missing or inconsistent values from table structure, reconcile related files, and leave ambiguous cases unchanged instead of guessing.

## 1. Install

```bash
pip install csv-consistency-repair
```

## 2. Repair a CSV

```bash
csv-consistency-repair input.csv -o repaired.csv --report repair.json
```

This is the normal starting point. Keep `input.csv`; the repaired file is written separately.

## 3. Ask for broader conservative repair

```bash
csv-consistency-repair input.csv -o repaired.csv --report repair.json --safe
```

Use `--safe` when the file contains missing values, formulas, repeated relationships, grouped rules, time/sequential data, or other structural inconsistencies.

## 4. Very large files

For large files that only need local cleanup such as whitespace, optional null/boolean normalization, or exact deduplication:

```bash
csv-consistency-repair input.csv -o repaired.csv --report repair.json --stream
```

The streaming path uses bounded memory, but it does not perform the full global relationship analysis.

## 5. Related CSV files

For several files that belong together, use bundle mode:

```bash
csv-consistency-repair bundle.json --bundle -o repaired_bundle --report bundle.report.json
```

A minimal bundle file looks like:

```json
{
  "datasets": [
    {"name": "customers", "input": "customers.csv"},
    {"name": "orders", "input": "orders.csv"}
  ]
}
```

## 6. Understand the result

Open `repair.json`. The most useful fields are:

- `final_status`: overall result;
- `committed_edits`: changes actually made;
- `remaining_issues`: problems left unresolved;
- `closure`: replay, roundtrip, format-preservation, and configured-scope checks.

A file can remain partly unchanged because the program does not have enough evidence to choose a unique repair. That is deliberate.

## 7. Undo

```bash
csv-consistency-repair repaired.csv --undo --report repair.json -o restored.csv
```

Keep the report if you may need to explain or reverse the repair later.

## Recommended habit

Use three files:

```text
input.csv          original
repaired.csv       repaired output
repair.json        explanation and undo record
```

Start with the normal command. Use `--safe` when you want broader structural repair. Use `--stream` only for very large files when local cleanup is enough.

For project attribution, see `AUTHOR.md`.
