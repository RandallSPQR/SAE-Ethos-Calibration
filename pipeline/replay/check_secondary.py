"""Same-pass capture check (27B parameterization, 2026-09-29): the secondary SAE's per-uid sums captured INSIDE the primary
replay (features_L<layer>/) must equal those from a dedicated SAE_ROLE=secondary replay of the same rows. A read-order error
in the same-pass capture (nnsight touches blocks in execution order) would show up here as different features or sums.

  python -m replay.check_secondary --same RUN/features_L53 --dedicated RUN/features_secondary_check [--rtol 1e-3]
Exit 0 on agreement; 1 otherwise (and the driver stops)."""
import argparse
import json
import sys
from pathlib import Path


def _sums(d):
    import pyarrow.parquet as pq
    out = {}
    for f in Path(d).rglob("*_uidsums.parquet"):
        for r in pq.read_table(f).to_pylist():
            out[(r["uid"], int(r["feature"]))] = float(r["sum_act"])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--same", required=True)
    ap.add_argument("--dedicated", required=True)
    ap.add_argument("--rtol", type=float, default=1e-3)
    a = ap.parse_args()
    same, ded = _sums(a.same), _sums(a.dedicated)
    uids = {u for u, _ in ded}
    same = {k: v for k, v in same.items() if k[0] in uids}
    keys_only_same = sorted(set(same) - set(ded))[:5]
    keys_only_ded = sorted(set(ded) - set(same))[:5]
    worst = max((abs(same[k] - ded[k]) / max(1e-6, abs(ded[k])) for k in set(same) & set(ded)), default=None)
    ok = bool(uids) and not keys_only_same and not keys_only_ded and worst is not None and worst <= a.rtol
    print(json.dumps({"ok": ok, "uids": len(uids), "pairs": len(set(same) & set(ded)), "worst_rel_diff": worst,
                      "only_same_pass": keys_only_same, "only_dedicated": keys_only_ded}, indent=1))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
