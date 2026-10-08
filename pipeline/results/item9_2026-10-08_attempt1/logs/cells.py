import json, glob, collections, sys
scen = sys.argv[1] if len(sys.argv) > 1 else ""
rows = []
for f in glob.glob("/workspace/t2/runs/*/generation/arm_a/*.jsonl"):
    if scen and scen not in f: continue
    for l in open(f):
        if l.strip(): rows.append(json.loads(l))
by = collections.defaultdict(collections.Counter); pf = collections.Counter(); n = collections.Counter()
for r in rows:
    k = (r["scenario"], r["variant"]); by[k][r["judge"]["label"]] += 1; n[k] += 1
    f = r["observed_facts"]; pf[k] += (f.get("bad_calls", 0) > 0 or f.get("unparseable_tool_blocks", 0) > 0)
for k in sorted(by):
    print(f"{k[0]:<26}{k[1]:<9}n={n[k]:<4}proto_fail_rows={pf[k]:<4}" + "  ".join(f"{l}={c}" for l, c in by[k].most_common()))
