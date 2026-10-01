"""Benign actions from T3 CONTROL rows of the dev runs, for an external false-positive-rate calibration set (2026-10-01,
Randall, for Codex). One line per tool call made in the CONTINUATION (from the decision point; the shared greedy prefix is
the same for every continuation of a cell and is left out) of a control row whose CURRENT label is benign. Excluded:
prefix-contaminated cells (labeler 2026-09-30.3), any non-benign or unclear row, every path outside the two dev runs below.
The held-out matched-pair set is not in pipeline/ and is never read.

  python results/fpr_calibration/extract_benign_actions.py
"""
import glob, json, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RES = HERE.parent
RUNS = [("gemma-3-27b-it", RES / "t4_27b_2026-09-30_t3/relabel_2026-10-01.2/generation/arm_a"),
        ("gemma-2-9b-it", RES / "t3_2026-09-28_joined/generation/arm_a")]
_TOOL_BLOCK = re.compile(r"```tool.*?(```|$)", re.S)


def main():
    out, n_rows = [], {}
    for model, d in RUNS:
        assert "held" not in str(d).lower(), d
        rows = [json.loads(l) for f in sorted(glob.glob(str(d / "*.jsonl"))) for l in open(f) if l.strip()]
        keep = [r for r in rows if r["variant"] == "control" and r["judge"]["taxonomy"] == "benign"
                and not (r.get("observed_facts") or {}).get("prefix_facts")]
        n_rows[model] = len(keep)
        for r in keep:
            msgs, dp = r["messages"], r["decision_point"]
            step = 0
            for i in range(dp, len(msgs)):
                m = msgs[i]
                if m.get("role") != "assistant":
                    continue
                for tc in m.get("tool_calls") or []:
                    res = msgs[i + 1].get("content", "") if i + 1 < len(msgs) and msgs[i + 1].get("role") == "tool" else ""
                    out.append({"model": model, "run_id": r.get("run_id"), "source_uid": r["uid"], "scenario": r["scenario"],
                                "seed": r["seed"], "variant": r["variant"], "label": r["judge"]["label"],
                                "taxonomy": r["judge"]["taxonomy"],
                                "labeler_rules": (r.get("observed_facts") or {}).get("labeler_rules"),
                                "message_index": i, "step": step, "tool": tc["name"], "args": tc.get("args"),
                                "assistant_text": _TOOL_BLOCK.sub("", m.get("content") or "").strip(),
                                "result": str(res)[:2000]})
                step += 1
    path = HERE / "benign_control_actions.jsonl"
    with open(path, "w") as f:
        for x in out:
            f.write(json.dumps(x) + "\n")
    import collections
    c = collections.Counter((x["model"], x["scenario"]) for x in out)
    print(f"{len(out)} actions from {n_rows} benign control rows -> {path}")
    for k, v in sorted(c.items()):
        print(f"  {k[0]:16} {k[1]:26} {v}")


if __name__ == "__main__":
    main()
