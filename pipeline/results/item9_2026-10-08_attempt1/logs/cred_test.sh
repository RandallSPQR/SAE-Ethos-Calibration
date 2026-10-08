set -o pipefail
cd /workspace/scenarios && source /workspace/venv/bin/activate && rm -rf build_cred && python scripts/render.py --arm a --scenario credential_obstacle --seeds 0-5 --out build_cred | tail -1
echo "== flake8 per rendered seed (control) + after the one-token fix:"
python3 - <<'PY'
import json, subprocess, pathlib, shutil
FIX = {0: ("import os\n", ""), 1: ("== None", "is None"), 2: ("== True", "is True")}
for sd in sorted(pathlib.Path("build_cred/discover/credential_obstacle").glob("seed_*")):
    man = json.loads((sd / "manifest.json").read_text()); v = int(man["surface"]["BUG_VARIANT"]); mod = man["surface"]["MODULE_B"] + ".py"
    src = (sd / "control" / mod).read_text()
    r = subprocess.run(["python", "-m", "flake8", mod], cwd=sd / "control", capture_output=True, text=True)
    before = [l.split(":", 3)[3].strip()[:44] for l in r.stdout.strip().splitlines()]
    find, repl = FIX[v]; assert src.count(find) == 1, (v, find, src.count(find))
    tmp = sd / "_fixed"; shutil.rmtree(tmp, ignore_errors=True); shutil.copytree(sd / "control", tmp); (tmp / mod).write_text(src.replace(find, repl))
    r2 = subprocess.run(["python", "-m", "flake8", mod], cwd=tmp, capture_output=True, text=True)
    print(f"{sd.name} v{v} {mod}: before={before} after={r2.stdout.strip() or 'clean'}")
PY
cd /workspace/pipeline && export HF_HOME=/workspace/hf LOCAL_API_KEY=x TOKENIZERS_PARALLELISM=false PYTHONUNBUFFERED=1 && rm -rf /workspace/t2_test
echo "== harness test, credential seeds 0-5, n=1:"
timeout 1500 python -m harness.run_harness --build ../scenarios/build_cred --runs-root /workspace/t2_test/runs --n 1 --scenarios credential_obstacle 2>&1 | grep -E "EXCLUDED|continuations written|harness:"
RUN=$(ls -d /workspace/t2_test/runs/*/ | head -1)
python3 - "$RUN" <<'PY'
import json, sys
c = json.load(open(sys.argv[1] + "/cardinality.json"))
print("generated", c["generated"], "| excluded prefixes", c["excluded_prefix_count"])
for k, v in (c.get("reach") or {}).items(): print(" ", k, v)
PY
