from __future__ import annotations
import argparse, json
from pathlib import Path
from .benchmarking import lock_benchmark_protocol, run_benchmark


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="csv-consistency-benchmark", description="Locked ground-truth benchmark harness for CSV repair.")
    p.add_argument("manifest")
    p.add_argument("--lock", help="Protocol lock JSON. Created if it does not exist; verified if it exists.")
    p.add_argument("--output", required=True, help="Benchmark result JSON.")
    args = p.parse_args(argv)
    lock = None
    if args.lock:
        lp = Path(args.lock)
        if lp.exists():
            lock = json.loads(lp.read_text(encoding="utf-8"))
        else:
            lock = lock_benchmark_protocol(args.manifest, lp)
    result = run_benchmark(args.manifest, lock)
    Path(args.output).write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"cases": result["summary"]["cases"], "protocol_sha256": result["protocol"]["protocol_sha256"], "output": args.output}, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
