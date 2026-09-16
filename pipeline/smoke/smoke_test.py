#!/usr/bin/env python3
"""End-to-end smoke test with NO heavy deps and NO GPU: proves the wiring from scenario -> seed ->
transcript -> resample -> gates holds together. On a real box you swap --mock for --go at each stage
and add the replay pass. This is the 'does the loop hold' check before renting anything.

  python smoke/smoke_test.py            # runs the full mock loop, then the fixture gates
"""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCEN = ROOT.parent / "scenarios"


def run(cmd, cwd=ROOT):
    print("  $", " ".join(str(c) for c in cmd))
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    print("   ", (r.stdout or r.stderr).strip().splitlines()[-1] if (r.stdout or r.stderr).strip() else "(ok)")
    if r.returncode != 0:
        print(r.stdout, r.stderr); sys.exit(1)
    return r


def main():
    tmp = Path(tempfile.mkdtemp())
    build = SCEN / "build"     # canonical build dir (fixtures read seed_000 from here)
    print("1. render scenarios (seeds 0-1)")
    run([sys.executable, "scripts/render.py", "--arm", "a", "--seeds", "0-1", "--out", str(build)],
        cwd=SCEN)
    print("2. build Petri seeds")
    run([sys.executable, "seeds/build_seeds.py", "--scenarios", str(SCEN), "--build", str(build),
         "--arm", "a", "--out", str(tmp / "seeds")])
    print("3. REAL executor path via scripted end-to-end fixtures (parse->run tool->mutate repo->label)")
    run([sys.executable, "-m", "harness.fixtures"])
    print("4. Petri path: run_petri dry run (exploration / Arm B), + a fixture transcript")
    run([sys.executable, "generate/run_petri.py", "--seeds", str(tmp / "seeds")])
    run([sys.executable, "generate/extract_transcripts.py", "--fixture", "--out", str(tmp / "transcripts")])
    print("5. resample at decision point (mock target)")
    run([sys.executable, "-m", "resample.resample", "--transcripts", str(tmp / "transcripts"),
         "--out", str(tmp / "resampled"), "--n", "8", "--mock"])
    print("6. replay (dry run — GPU stage)")
    run([sys.executable, "-m", "replay.replay", "--transcripts", str(tmp / "resampled"), "--features",
         str(tmp / "features")])
    print("7. gate logic (fixture mode, G0-G8)")
    run([sys.executable, "-m", "gates.run_gates", "--fixture"])
    print("\nSMOKE OK: scripted fixtures exercise the REAL executor + labeler; serializer, resample, "
          "replay-wiring, and all 9 gate logics connect.")
    print("Next on a real box: vLLM up, run the T1 ladder G0..G5, then harness --n, replay --go, run_gates.")


if __name__ == "__main__":
    main()
