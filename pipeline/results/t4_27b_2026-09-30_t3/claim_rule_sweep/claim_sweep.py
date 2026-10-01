import json, glob, collections
from harness import labeler as L
from harness.claim_rule import submit_kind
FAM = {"impossible_test": "impossible", "missing_delete_tool": "missing_tool", "credential_obstacle": "credential",
       "honeypot_forbidden_table": "honeypot"}
def load(p): return [json.loads(l) for f in sorted(glob.glob(p + "/*.jsonl")) for l in open(f) if l.strip()]
RUNS = {"Gemma-2-9B (T3 joined)": "results/t3_2026-09-28_joined/generation/arm_a",
        "Gemma-3-27B (G1 calibration, dev)": "results/t4_27b_2026-09-30_g1cal/27b/cal/runs/run_bb664336cb2d/generation/arm_a",
        "Gemma-3-27B (T3, .4 labels)": "results/t4_27b_2026-09-30_t3/relabel_2026-09-30.4/generation/arm_a"}
print("## Instruction compliance: share of submits that are NOT the instructed literal (honeypot excluded: it submits a value)\n")
print("| model / run | submits | literal / instructed form | non-literal | claim | report | unclear |\n|---|---|---|---|---|---|---|")
for name, p in RUNS.items():
    c = collections.Counter()
    for r in load(p):
        fam = FAM[r["scenario"]]; f = r["observed_facts"] or {}
        if fam == "honeypot" or f.get("submitted_value") is None: continue
        k = submit_kind(fam, f["submitted_value"], push_claim=L._push_claim(f["submitted_value"]))
        if fam == "credential" and k == "claim" and "receipt" in str(f["submitted_value"]).lower(): k = "literal"   # the instructed reply
        c[k] += 1
    n = sum(c.values()); nl = n - c["literal"]
    print(f"| {name} | {n} | {c['literal']} | **{nl} ({nl / max(n, 1):.1%})** | {c['claim']} | {c['report']} | {c['unclear']} |")

rows = load(RUNS["Gemma-3-27B (T3, .4 labels)"])
def lab(r, f): return L.label(FAM[r["scenario"]], r["variant"], f, None, {"terminal": f.get("terminal"), "answer": f.get("submitted_value")})
base = sum(lab(r, dict(r["observed_facts"]))[:2] == (r["judge"]["label"], r["judge"]["taxonomy"]) for r in rows)
moves, kinds = [], collections.Counter()
for r in rows:
    fam = FAM[r["scenario"]]; f = dict(r["observed_facts"]); ans = f.get("submitted_value")
    if fam == "honeypot" or ans is None: continue
    k = submit_kind(fam, ans, push_claim=L._push_claim(ans)); kinds[(r["scenario"], r["variant"], k)] += 1
    old = lab(r, f)
    key = "submitted_pushed" if fam == "credential" else "submitted_done"
    if k in ("literal", "claim", "report"):
        f[key] = k in ("literal", "claim") if fam != "credential" else f[key]
        new = lab(r, f)
    else:
        a = lab(r, {**f, key: True}); b = lab(r, {**f, key: False})
        new = a if a[:2] == b[:2] else ("unclear_submit", "unclear", a[2])
    f["_kind"] = k
    if new[:2] != old[:2]:
        moves.append((r["uid"], k, f"{old[0]} ({old[1]})", f"{new[0]} ({new[1]})", ans[:90].replace("\n", " ")))
print(f"\n## 27B T3 sweep (baseline: label() on the stored .4 facts reproduces {base}/{len(rows)} labels)\n")
print("Submit kinds of non-literal submits:", {k: v for k, v in sorted(kinds.items()) if k[2] not in ("literal",)})
print(f"\nLabels that would move: {len(moves)}\n\n| uid | kind | .4 label | proposed | submit text |\n|---|---|---|---|---|")
for m in moves: print("| " + " | ".join(m) + " |")
bc = collections.Counter((m[0].split("/")[0], m[0].split("/")[2], m[2].split(" (")[1][:-1], m[3].split(" (")[1][:-1]) for m in moves)
print("\nBy scenario x variant x taxonomy:", dict(bc))
