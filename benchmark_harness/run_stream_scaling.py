import argparse, csv, json, tempfile
from pathlib import Path
from csv_consistency_repair import stream_repair, StreamRepairConfig

def main():
    ap=argparse.ArgumentParser(description='Generate and benchmark a bounded-memory local-repair CSV workload.')
    ap.add_argument('--rows',type=int,default=1_000_000,help='Rows to generate (use 10000000 to reproduce the published 10M scale).')
    ap.add_argument('--keep',type=Path,default=None,help='Optional directory to keep generated files and report.')
    args=ap.parse_args()
    holder=tempfile.TemporaryDirectory() if args.keep is None else None
    root=args.keep if args.keep is not None else Path(holder.name); root.mkdir(parents=True,exist_ok=True)
    dirty=root/'scale.dirty.csv'; out=root/'scale.fixed.csv'; rep=root/'scale.report.json'
    with dirty.open('w',newline='') as f:
        w=csv.writer(f); w.writerow(['id','flag','note'])
        for i in range(args.rows): w.writerow([i,' YES ' if i%2 else ' no ',f' row {i} '])
    r=stream_repair(dirty,out,rep,StreamRepairConfig(normalize_booleans=True,journal_edits=False,verify_replay=True))
    print(json.dumps({k:r[k] for k in ['rows_read','rows_written','edits','replay_pass','seconds','rows_per_second','input_sha256','output_sha256']},indent=2))
if __name__=='__main__': main()
