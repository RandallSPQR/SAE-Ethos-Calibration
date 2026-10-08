import json, sys
run = sys.argv[1]
c = json.load(open(run + "/cardinality.json"))
print("generated", c["generated"], "| excluded prefixes", c["excluded_prefix_count"])
for k, v in c["reach"].items():
    print(f"  {k:<36} reached {v['reached']}/{v['attempted']}  no_nudge {v['reached_no_nudge']}  p={v['p_reach']}")
m = json.load(open(run + "/manifest.json"))
print("manifest git:", m.get("git_commit"), "code_hash:", m.get("code_hash"))
iso = m.get("isolation") or {}
print("isolation:", iso.get("mechanism"), iso.get("canaries"), "landlock:", (iso.get("tried") or {}).get("seccomp_uid", {}).get("landlock_abi"))
print("model rev:", (m.get("model") or {}).get("revision"), "weight_hash:", (m.get("model") or {}).get("weight_hash"))
