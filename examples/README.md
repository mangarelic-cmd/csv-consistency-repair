# Example

Run:

```bash
csv-consistency-repair dirty.csv -o repaired.csv --report repair_report.json \
  --deduplicate --normalize-null-markers --normalize-booleans
```

The tool will trim safe outer whitespace, normalize configured null/boolean forms, remove exact duplicates when that lowers the global consistency score, and retain non-deterministic anomalies such as `MAYBE` for review.
