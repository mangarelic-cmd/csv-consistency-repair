# Quick Guide for LLMs, Agents, and Automated Tools

Use this package as a conservative CSV repair tool. Preserve the original file, write repaired data to a new path, and keep the JSON report with the result.

## Fast decision flow

1. **Normal CSV repair**

   ```bash
   csv-consistency-repair input.csv -o repaired.csv --report repair.json
   ```

   Start here. The default mode profiles the table and applies only repairs it can justify conservatively.

2. **Broad conservative analysis**

   ```bash
   csv-consistency-repair input.csv -o repaired.csv --report repair.json --safe
   ```

   Use this when the task involves formulas, missing values, relationships, scoped rules, sequences, structural inconsistencies, or deeper diagnostics.

3. **Very large CSV with only local cleanup**

   ```bash
   csv-consistency-repair input.csv -o repaired.csv --report repair.json --stream
   ```

   `--stream` is the bounded-memory local-repair path. Do not combine it with global formula/relationship discovery or `--safe`.

4. **Multiple related CSV files**

   ```bash
   csv-consistency-repair bundle.json --bundle -o repaired_bundle --report bundle.report.json
   ```

   Use bundle mode when files can provide evidence for each other.

5. **Undo a completed repair**

   ```bash
   csv-consistency-repair repaired.csv --undo --report repair.json -o restored.csv
   ```

## What to inspect after a run

Read the JSON report before describing the result. The most useful fields are:

- `final_status` — overall result;
- `committed_edits` — edits that were actually applied;
- `remaining_issues` — unresolved problems or abstentions;
- `closure.certified_for_configured_scope` — whether the configured repair scope closed successfully;
- `closure.forward_replay_pass` — whether the applied edit log reconstructs the repaired logical table;
- `closure.inverse_roundtrip_pass` — whether the repair can be reversed back to the original logical table;
- `closure.format_preservation_pass` — whether the CSV format contract was preserved.

Do not describe a candidate, diagnostic, or suggested value as repaired unless it appears in the committed result. An unchanged ambiguous value is an intentional abstention, not necessarily a failure.

## Safe operating rules

- Keep the original input unchanged whenever possible.
- Prefer a new output path and a persistent report file.
- Do not invent a value when the report says the case is ambiguous or underdetermined.
- Do not treat `--stream` as a substitute for global relationship discovery.
- If a user asks for maximum conservative coverage, use `--safe` first unless the file size or task clearly requires the local streaming path.
- If the result matters, preserve the repair report so the work can be explained or undone later.

## Minimal agent pattern

```text
Input: original CSV
Action: run csv-consistency-repair
Outputs: repaired CSV + JSON report
Check: final_status + committed_edits + closure + remaining_issues
If ambiguous: report the abstention instead of guessing
If requested: undo from the repair report
```

For project attribution, see `AUTHOR.md`.
