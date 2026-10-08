import json, glob, collections, re
TOOL_RE = re.compile(r"```tool\s*(\{.*?\})\s*```", re.S)
rows = []
for f in glob.glob("/workspace/t2/runs/*/generation/arm_a/honeypot*.jsonl"):
    for l in open(f):
        if l.strip(): rows.append(json.loads(l))
ctl = [r for r in rows if r["variant"] == "control"]
print("== control incomplete: (last 3 tools, tests_pass, unparseable, bad_calls)")
for r in ctl:
    if r["judge"]["label"] == "incomplete":
        f = r["observed_facts"]; print("  ", f["tool_history"][-3:], "tests:", f["final_tests_pass"], "unp:", f["unparseable_tool_blocks"], "bad:", f["bad_calls"])
print("\n== unparseable blocks in control (first 6):")
n = 0
for r in ctl:
    for m in r["messages"][r["decision_point"]:]:
        if m.get("role") != "assistant" or "```tool" not in m.get("content", ""): continue
        mm = TOOL_RE.search(m["content"]); body = mm.group(1) if mm else m["content"][m["content"].find("```tool"):][:240]
        try: json.loads(body); continue
        except Exception:
            if n < 6: print("  ", body[:240].replace("\n", " ")); n += 1
print("\n== edit_file calls in control: find/replace samples + results")
n = 0
for r in ctl:
    msgs = r["messages"]
    for i, m in enumerate(msgs[r["decision_point"]:], r["decision_point"]):
        for tc in m.get("tool_calls") or []:
            if tc["name"] == "edit_file" and n < 8:
                res = msgs[i + 1].get("content", "")[:60].replace("\n", " ") if i + 1 < len(msgs) else ""
                print("  ", json.dumps(tc["args"])[:200], "->", res); n += 1
print("\n== asked (control) questions:")
for r in ctl:
    if r["judge"]["label"] == "asked":
        for m in r["messages"][r["decision_point"]:]:
            for tc in m.get("tool_calls") or []:
                if tc["name"] == "ask_user": print("  ", str(tc["args"].get("question"))[:150].replace("\n", " ")); break
print("\n== control tool histories (all):")
for r in ctl: print("  ", r["judge"]["label"], r["observed_facts"]["tool_history"])
