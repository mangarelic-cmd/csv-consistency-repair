import json, statistics, time
from pathlib import Path
from csv_consistency_repair import repair, RepairConfig
from csv_consistency_repair.io import read_table

ROOT = Path(__file__).resolve().parents[1] / "benchmark_evidence"
LOCK = json.loads((ROOT / "FUNCTIONAL_MANIFEST_LOCK.json").read_text())
OUT = ROOT / "current_outputs"
OUT.mkdir(exist_ok=True)

def resolve(path):
    p = Path(path)
    return p if p.is_absolute() else ROOT / p

def mapp(t):
    d = {(-1, j): v for j, v in enumerate(t.header)}
    for i, row in enumerate(t.rows):
        for j, v in enumerate(row):
            d[(i, j)] = v
    return d

def metrics(dirty, repaired, clean):
    d, r, c = map(read_table, [dirty, repaired, clean])
    dm, rm, cm = mapp(d), mapp(r), mapp(c)
    ks = set(dm) | set(rm) | set(cm)
    bad = {k for k in ks if dm.get(k) != cm.get(k)}
    touch = {k for k in ks if dm.get(k) != rm.get(k)}
    good = {k for k in touch if k in bad and rm.get(k) == cm.get(k)}
    repairedtruth = {k for k in bad if rm.get(k) == cm.get(k)}
    false = {k for k in touch if k not in bad or rm.get(k) != cm.get(k)}
    return {
        "truth_bad": len(bad), "touched": len(touch), "good_touches": len(good),
        "repaired_truth": len(repairedtruth), "false_mutations": len(false),
        "precision": len(good) / len(touch) if touch else 1.0,
        "recall": len(repairedtruth) / len(bad) if bad else 1.0,
        "exact": r.logical_digest() == c.logical_digest(),
    }

runs = []
for case in LOCK["cases"]:
    dirty, clean = resolve(case["dirty"]), resolve(case["clean"])
    rec = {"id": case["id"], "family": case["family"]}
    for mode, cfg in [("configured", RepairConfig(**case["config"])), ("zero", RepairConfig())]:
        out = OUT / f"{case['id']}.{mode}.csv"
        report = OUT / f"{case['id']}.{mode}.json"
        t0 = time.perf_counter()
        rr = repair(dirty, out, report, cfg)
        z = metrics(dirty, out, clean)
        z.update(seconds=time.perf_counter()-t0, edits=rr.committed_edits, status=rr.final_status,
                 forward=rr.report["closure"]["forward_replay_pass"],
                 inverse=rr.report["closure"]["inverse_roundtrip_pass"])
        rec[mode] = z
    runs.append(rec)
    print(case["id"], rec["configured"]["exact"], rec["zero"]["exact"], flush=True)

def summary(mode):
    xs = [r[mode] for r in runs]
    tb = sum(x["truth_bad"] for x in xs); touch = sum(x["touched"] for x in xs)
    good = sum(x["good_touches"] for x in xs); rep = sum(x["repaired_truth"] for x in xs)
    false = sum(x["false_mutations"] for x in xs)
    return {"cases": len(xs), "truth_bad": tb, "touched": touch, "good_touches": good,
            "repaired_truth": rep, "false_mutations": false,
            "precision_micro": good/touch if touch else 1.0, "recall_micro": rep/tb if tb else 1.0,
            "exact_datasets": sum(x["exact"] for x in xs),
            "exact_rate": sum(x["exact"] for x in xs)/len(xs),
            "median_s": statistics.median(x["seconds"] for x in xs),
            "total_s": sum(x["seconds"] for x in xs)}
S = {"configured": summary("configured"), "zero_config": summary("zero")}
(ROOT / "FUNCTIONAL_CURRENT_RESULTS.json").write_text(json.dumps({"summary":S,"runs":runs},indent=2)+"\n")
print(json.dumps(S, indent=2))
