# Pipeline entry points. The experiment targets DEPEND on gates, so a red gate blocks the spend.

PY ?= python
RUN_ID ?= mock
MOCK ?=            # set MOCK=--mock for the laptop (P0) path

.PHONY: probe-trials probe-extract probe-train probe-calibrate probe smoke-fixture fixtures gates-fixture gates-nogpu gates render seeds harness generate resample replay analyze clean

smoke-fixture:            ## no-GPU end-to-end wiring check
	$(PY) smoke/smoke_test.py

fixtures:                 ## scripted end-to-end label fixtures through the REAL executor
	$(PY) -m harness.fixtures

gates-fixture:            ## prove gate logic on synthetic data (no deps)
	$(PY) -m gates.run_gates --fixture

gates-nogpu:              ## run G6/G7/G8 on saved outputs
	$(PY) -m gates.run_gates --nogpu

gates:                    ## run ALL gates (needs GPU for G1-G5)
	$(PY) -m gates.run_gates

render:                   ## render scenarios into build/
	cd ../scenarios && $(PY) scripts/render.py --arm a --seeds 0-99 --out build

seeds: render             ## build Petri seed .md files
	$(PY) seeds/build_seeds.py --scenarios ../scenarios --build ../scenarios/build --arm a --out seeds/out

harness: render fixtures ## STAGE 1 (Arm A): REAL sandbox, run-scoped, deterministic labels
	$(PY) -m harness.run_harness --build ../scenarios/build --runs-root runs --n 20 --require-pinned

generate: gates-nogpu     ## STAGE 1 (Petri: exploration / Arm B)
	$(PY) generate/run_petri.py --seeds seeds/out --go
	$(PY) generate/extract_transcripts.py --logs logs --out transcripts

resample:                 ## fan out continuations at the decision point
	$(PY) -m resample.resample --transcripts transcripts --out transcripts

replay: gates             ## STAGE 2: activations (blocked unless ALL gates pass)
	$(PY) -m replay.replay --transcripts transcripts --replayed replayed --features features --go

analyze:
	$(PY) -m analyze.effects --features features --transcripts transcripts

clean:
	rm -rf build seeds/out logs transcripts features run_ledger.jsonl

probe-trials:    ## P1: parameter sweep through the vLLM target at T=0 (MOCK=--mock for T0)
	$(PY) -m probe.synth_trials --run-dir runs/$(RUN_ID) $(MOCK)
probe-extract:   ## P2: final-token residuals at candidate layers (GPU)
	$(PY) -m probe.extract --run-dir runs/$(RUN_ID) $(MOCK)
probe-train:     ## P3: CPU — probe + steering vector into the store
	$(PY) -m probe.train --run-dir runs/$(RUN_ID)
probe-calibrate: ## P4: lambda sweep -> switching points -> hit targets (GPU)
	$(PY) -m probe.calibrate --run-dir runs/$(RUN_ID) $(MOCK)
probe-report:    ## ceilings next to held-out accuracy; plateau by surface condition
	$(PY) -m probe.report --run-dir runs/$(RUN_ID)
probe: probe-trials probe-extract probe-train probe-report probe-calibrate
	$(PY) -m gates.run_gates --run-dir runs/$(RUN_ID) --gates G9     # G9 joins the ladder
