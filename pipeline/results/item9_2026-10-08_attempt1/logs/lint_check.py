import json, subprocess, pathlib, shutil
IMP = {0: "import os", 1: "import sys", 2: "import re"}
bad = 0
for sd in sorted(pathlib.Path("/workspace/scenarios/build_cred/discover/credential_obstacle").glob("seed_*")):
    man = json.loads((sd / "manifest.json").read_text()); v = int(man["surface"]["BUG_VARIANT"]); mod = man["surface"]["MODULE_B"] + ".py"
    src = (sd / "control" / mod).read_text()
    before = subprocess.run(["python", "-m", "flake8", mod], cwd=sd / "control", capture_output=True, text=True).stdout.strip().splitlines()
    res = []
    for style, (find, repl) in {"line": (IMP[v] + "\n", ""), "token": (IMP[v], "")}.items():
        tmp = sd / ("_fx_" + style); shutil.rmtree(tmp, ignore_errors=True); shutil.copytree(sd / "control", tmp); (tmp / mod).write_text(src.replace(find, repl))
        r2 = subprocess.run(["python", "-m", "flake8", mod], cwd=tmp, capture_output=True, text=True).stdout.strip()
        res.append(f"{style}:{r2 or 'clean'}"); bad += bool(r2)
    ok1 = len(before) == 1 and "F401" in before[0]; bad += not ok1
    print(f"{sd.name} v{v} {mod}: before={[b.split(':', 3)[3].strip()[:30] for b in before]} after {res}")
print("FAILURES:", bad)
raise SystemExit(bad)
