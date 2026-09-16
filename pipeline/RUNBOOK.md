# RUNBOOK — first GPU session, fail-fast and cheap

The governing rule: **do every step on the cheapest resource that can prove it, and put a STOP gate
before every increase in cost.** Most mistakes on a first run are wiring mistakes, and wiring is free
to debug. You should not touch a paid GPU until the loop already runs end-to-end on your laptop, and
you should not scale generation until one transcript has been replayed correctly.

Cost tiers, cheapest first: **T0** your machine ($0) → **T1** one cheap pod, terminated within the hour
(~$0.33–0.79/hr, A6000/L40S) → **T2** short 9B generation on the same cheap pod → **T3** full 9B run →
**T4** (optional) 27B on an 80GB card. You do not advance a tier until the previous tier is green.

Two money-savers that matter more than card choice:
- **Network volume.** Keep weights, SAE, oracle, Milvus index, code, and the conda env on the
  $0.07/GB/mo volume. Then terminating the pod costs you nothing but the volume, and a restart is
  minutes. **Terminate the pod between every work burst.** Your design is bursty (generate → analyze
  offline → generate), so idle GPU time is the main way this budget leaks.
- **A RunPod account spending limit.** Set it to your total budget. It is the backstop behind the
  pipeline's own `cost.ceiling_usd` ($250) kill switch. Belt and suspenders.

---

## T0 — on your machine, $0. Do ALL of this before renting anything.

```
# in the repo (scenarios/ and pipeline/ side by side)
cd pipeline
python -m gates.run_gates --fixture      # 8/8 — proves gate logic
python smoke/smoke_test.py               # proves scenario -> seed -> transcript -> resample -> gates
cd ../scenarios && python scripts/validate.py --arm a   # 4/4 scenarios solvable
```

STOP if any of these is red. Fix it here where it's free. When they're all green, you have proven
everything except the four GPU-bound stubs.

Also at T0: fill the blanks in `config/models.yaml` you already know —
`oracle.hf_id` (exact Karvonen 9B adapter), and from the Gemma Scope release card the
`sae.published.l0` and `sae.published.fvu` numbers and the real `sae.hook_point` module path. If you
don't have the published numbers yet, that's fine — G2 will say `hook UNVERIFIED` rather than lie.

---

## T1 — one cheap pod, ONE hour, the make-or-break hour (~$0.80)

Goal: prove the four stubs on the smallest possible workload, then terminate. Nothing here needs full
scenarios or batches. If T1 fails you've spent under a dollar.

### Pod template
- GPU: **L40S 48GB** ($0.79) or **A6000** ($0.33) — either fits 9B + SAE + oracle in one worker.
- Image: the RunPod **PyTorch** base (CUDA matched to the image; don't fight it).
- Mount the **network volume at `/workspace`**. Expose **SSH** (work from VS Code/Cursor over SSH, not
  the notebook UI). Jupyter optional.
- Startup: nothing heavy — you install into the volume once, below.

### One-time install (into the volume, so it survives termination)
```
cd /workspace
python -m venv venv && source venv/bin/activate      # lives on the volume; reused next pod
pip install -U pip
# pin against what the image ships; start from the pipeline extras:
pip install "nnsight" "sae-lens" "transformers" "peft" "pyarrow" "openai" "pyyaml"
pip install vllm                                     # the served target
# Arm B / HARP only when you get there:  pip install pymilvus
huggingface-cli login                                # gated Gemma
```
Download weights once to the volume: `google/gemma-2-9b-it`, the Gemma Scope 9B-IT residual SAE, the
Karvonen oracle adapter. After this, a fresh pod skips all of it.

### The calibration ladder G0→G5, in cost order (cheapest signal first)

Run these **in order** on the dev model, before any behavioral scenario. Each is a checksum on the
instrument; a red one means the microscope is out of focus and nothing downstream can be trusted. Do
them at **temperature 0** (set `sampling.temperature: 0` for calibration).

0. **Serve the model, then G0 model checksum.** Bring up vLLM (`served_model_name: gemma-2-9b-it`,
   port 8000); confirm one completion. Both the serving path and the nnsight path must build the prompt
   with the ONE canonical serializer (`model_io/gemma2.py` — no system role, alternating turns, tool
   results as user turns; Gemma-2's stock template rejects a system role and non-alternating turns).
   Generate one prompt greedily through each path and confirm identical token ids
   (`features/model_checksum.json`). **STOP if they differ** — either the serializer isn't shared
   (prompt differs) or the computation differs; the manifest's prompt hash tells you which.

1. **G1 exact replay checksum (T=0).** Generate one known transcript **greedily**, replay it
   teacher-forced, and require `sampled_ids == replay argmax` at ~100%. This is a real checksum, far
   stronger than "90% seems plausible." **STOP if it's not exact** — the chat template or tokenization
   in replay differs from what the model saw, and every downstream activation is off. Only after G1 is
   exact do you switch behavioral generation to T=0.8 (where G1 auto-switches to its logprob criterion).

2. **G2 hook-point identification.** Fill `replay/sae.py: hook_identification_report()` and the
   `resid_post()` transform in `hooks.py` (remember: hook_resid_post is NOT `layer.output[0]` — a
   Gemma-2 block returns `(hidden_states, residual)` and the stream is their sum). Load the **canonical
   layer-31 width-16k** SAE. Run health at the chosen hook AND every decoy candidate.
   **STOP hard if G2 is red or UNVERIFIED.** Only the trained tensor reproduces Google's FVU/L0; if a
   decoy also matches, the hook is ambiguous. This is the check that saves a thousand wasted transcripts.

3. **G3 known-feature checksum — the embarrassingly obvious first experiment.** Before any behavioral
   scenario, prove the whole chain `model → tokenization → hook → residual → SAE → feature` on a known
   anchor. Take a Neuronpedia-labeled **code feature** at layer 31 / gemmascope-res-16k (verify the
   exact index on Neuronpedia — e.g. a code-documentation feature) and confirm it fires more on:
   ```python
   def fibonacci(n):
       """Return the nth Fibonacci number."""
       ...
   ```
   than on matched ordinary prose. **STOP if the code feature doesn't discriminate** — if this fails,
   the rig is wrong and no behavioral result would mean anything. This one test isolates the microscope
   from every behavioral confound (judges, scenarios, Petri, steering).

4. **G4 causal checksum.** Clamp/steer one known feature and reproduce a simple, obvious effect with a
   dose-response curve in fraction-of-mean-norm units. If you can't move something easy, you can't
   interpret a null on something hard.

5. **G5 oracle checksum.** Confirm the Karvonen oracle describes a known code activation sensibly and
   does *not* confabulate confidently on zeroed input. The confab rate sets how much weight the oracle
   gets in the writeup.

### T1 under gate rules 2026-09-16.2 (after the first full run — see gates/CHANGELOG.md)

The first full run (2026-09-16, artifacts in `pipeline/results/t1_2026-09-16/`) found four wiring bugs
and three instrument findings; the rules were corrected and versioned. Next pod session, in this order:

```
bash calibrate/run_t1.sh /workspace/t1 --fp32   # G1 in float32 on BOTH paths: if the near-tie flip vanishes, it was numerical
bash calibrate/run_t1.sh /workspace/t1          # bf16 ladder G0-G5 + the TransformerLens identity stage (stage 3)
bash calibrate/run_probe.sh t1probe             # only if G0-G3 are green: P1-P4 + G9
```
What the new stages settle: G1's flip excuse is conditional on generation's own top-2 margin (a wide-margin
flip still fails); G2 identifies the hook by variance-explained margin, checks JumpReLU encode integrity,
measures L0 per Pile document (own BOS, 1024 ctx, BOS excluded) and compares the captured tensor to
TransformerLens `blocks.31.hook_resid_post` loaded with no weight processing; G3 scores window-max; G4
reads out at a live decision point; G5 is a paired real-vs-null discrimination test. Every report carries
`rules` and the manifest carries `gate_rules_version`.

### End of T1
Snapshot the working venv state (it's on the volume already). **Terminate the pod.** You now know the
instrument is focused. Total spend so far: about a dollar, plus pennies of volume.

STOP-if tripwires for T1 (any one → terminate, fix at T0/offline, come back):
- vLLM won't serve → config/CUDA problem, not a research problem.
- G0 divergence → serving path and observation path aren't the same computation.
- G1 not exact at T=0 → template/tokenization mismatch (the classic silent killer).
- G2 red/UNVERIFIED → wrong hook, wrong SAE id, or missing published numbers.
- G3 code feature doesn't discriminate → the microscope is wrong; do not proceed to behavior.
- 90 minutes in and still debugging install → terminate, fix the venv notes offline, restart fresh
  (the volume kept your downloads).

## T1.5 — Probe track: the external fixture (~$2–4, same cheap pod, can share the T1 session)

Only after G0–G3 are green. A probe is a one-direction custom dictionary: hundreds of labeled trials
instead of hundreds of millions of tokens, and its unit direction is a steering vector like any other.
Fan et al. (2026, arXiv:2609.16436) published the target: on Llama-3.3-70B a final-token logistic probe
(82% held-out) steered the lottery switching point across 30–200 tokens with ~2-token MAE. We reproduce
the SHAPE on Gemma-2-9B. Their absolute numbers are Llama's; ours are reported beside them, not gated.

P0 (laptop, $0):  make probe RUN_ID=mock MOCK=--mock   → G9 fixture logic green, CPU path end-to-end.
P1 (pod, minutes): make probe-trials  — 2 tasks × ~35 grid points × 8 seeds at T=0 ≈ 600 short
                   completions. STOP if >5% unparsed — fix the prompt offline, not on the meter.
                   Read baseline.json: the unsteered curve must cross 0.5 inside the grid.
P2 (pod, minutes): make probe-extract — residuals at layers 20/26/31 via the SAME resid_post()
                   transform G2 verified. Never bare output[0].
P3 (offline, $0):  make probe-train — pick layer/C by CV, report HELD-OUT accuracy.
                   STOP if < 0.75: the trait isn't linearly accessible here; nothing to calibrate.
P4 (pod, ~20 min): make probe-calibrate — λ sweep through hooks.teacher_forced_forward(steer=...),
                   the G4 path. Then invert to hit the target switching points and re-run once each.
G9:                monotone λ→sp, MAE ≤ 5 tokens, coverage ≥ 60% of the target span. Terminate.

What a green G9 buys: a calibrated dial ("risk-tolerant at switching point 60") instead of a persona
string, on an instrument someone else measured. What a red G9 tells you: which of (trait not linear
here / injection path / grid mis-scaled) failed — each has its own STOP above. Do not "tune to green".

---

## T2 — short 9B generation, still the cheap pod (~$3–5)

Only after T1 is green. Generate a *pilot* with the **real harness** (Arm A): seeds 0–4, all four
scenarios, ~10 continuations each. Labels come from real final state, so base rates are trustworthy.

```
make render                                          # renders the real repos to build/
python -m harness.run_harness --build ../scenarios/build --out transcripts --n 10   # add --mock to dry-run
python -m gates.run_gates --nogpu --transcripts transcripts   # G6 judge-agreement, G7 base rates
```
(Petri is not on this path. Use it separately, `generate/run_petri.py`, for exploratory scenario
discovery or Arm-B conversational audits.)

STOP and retune the scenario (offline, free) if **G7** shows any scenario outside the [0.10, 0.70]
transgression band or a control success below 0.5 — a scenario stuck at 0% or 100% gives no contrast
and is not worth replaying. Hand-label ~50 of these pilot transcripts now; that's your **G6** set and
it's reusable forever. **Terminate the pod.**

---

## T3 — full 9B run (~$20–40)

Only after T2's scenarios all pass G7 and G6 κ ≥ 0.70. Scale with the **same real harness** — NOT Petri.
The harness does its own decision-point sampling (deterministic prefix to a STRUCTURED trigger →
snapshot → N restored continuations), so you do **not** run `resample.resample` on Arm-A transcripts;
that's only for Petri/Arm-B. **Pin provenance first**: fill `models.yaml` revisions and the box-side
hashes, then `--require-pinned` refuses to run on any floating/null identity field.
```
make render
# 1) resolve a pinned, content-addressed manifest (fill models.yaml revisions + box-side hashes first):
python -m provenance resolve --scenarios ../scenarios --out /tmp/manifest.json   # prints run_id = H(manifest)
# 2) everything is run-scoped under runs/<run_id>/ so two runs can never be joined by accident:
python -m harness.run_harness --build ../scenarios/build --runs-root runs --n 20 --require-pinned
python -m replay.replay --run-dir runs/<run_id> --go
python -m gates.run_gates --run-dir runs/<run_id>          # ALL NINE (G0-G8)
python -m analyze.effects --features runs/<run_id>/features --transcripts runs/<run_id>/generation
```
Lineage is separate, immutable, and run-scoped: `runs/<run_id>/{generation,replay,features,analysis}`,
with `manifest.json` and `cardinality.json` at the root. Generation records (labels + `observed_facts`)
are never overwritten; replay writes token metadata to `replay/`, joined by uid; the feature store is a
third artifact. The logical key is `(run_id, uid)` and readers **refuse duplicate uids** — no
last-write-wins. Every stage does **cardinality accounting**: the harness enumerates excluded cells and
records `p_reach` per cell (the estimand is P(transgress | greedy prefix reached obstacle) — report it
alongside P(reach)); replay asserts in==out; G1 requires 100% replay coverage; G8/effects **fail on
missing token metadata**. Two labels per continuation — `decision_action_label` (the first choice) and
`episode_outcome_label` (how it ended); the default analysis pairs decision-span activations with the
outcome label (the prediction question A_decision → Y_eventual), and scoring the transgression span is a
separate experiment. At n≈400 per cell G8 runs the real aggregation under permuted labels — a red G8
means the machinery itself leaks structure. Do not report effects until it's green.

Terminate. Do the analysis and the four card-figure reproductions offline from the feature store —
**that's free.**

---

## T4 — 27B, optional, only if the 9B run motivates it (~$60–120)

Same pipeline, one config edit (`models.yaml` → 27B target, base, SAE release+id, oracle adapter, and a
new hook block with the 27B layer and its own `published` FVU/L0 — the hook is NOT the same layer).
**Re-run the full G0–G5 ladder on 27B** before scaling: new model, new hook, new fidelity check, and a
fresh provenance manifest. The 80GB A100 tier (~$1.19–1.39) is the only place you need it. Everything
you learned at 9B transfers except the numbers you must re-verify. Re-run the probe track too — the probe
layer is model-specific; re-sweep `layer_candidates`, do not carry 31 over.

---

## The five habits that keep failure cheap

1. **Terminate between bursts.** The volume remembers; the meter shouldn't run while you think.
2. **One before many.** One transcript through G1/G2 before a batch; one pilot before the full run.
3. **Verify the hook before you generate.** G2 green is a precondition, not a postcondition.
4. **Fidelity before analysis.** G1 green means the tokens are real; nothing downstream matters if not.
5. **Retune scenarios offline.** G7 failures are fixed in `scenarios/` on your laptop for $0, then
   re-rendered — never by burning GPU hours poking at prompts on a live pod.

Rough total to a complete 9B result with all four figures: **well under $50.** The budget headroom is
your permission to make mistakes — spend it on a second 27B pass or a steering sweep, not on idle pods.
