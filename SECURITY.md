# Security

`csv-consistency-repair` treats CSV cell content as data. It does not execute spreadsheet formulas, Python expressions, shell commands, or code embedded in cells.

Rules files are parsed as JSON and support only the documented operations. Row formulas use a fixed operation set (`sum`, `product`, `difference`, `ratio`); arbitrary expression evaluation is not supported.

The current input contract is UTF-8 CSV. Malformed CSV and non-UTF-8 input are rejected rather than silently reinterpreted.

For untrusted data, run the package with ordinary operating-system permissions appropriate to the files being processed. Foreign-key rules can read the explicit reference files named by the rules document.
