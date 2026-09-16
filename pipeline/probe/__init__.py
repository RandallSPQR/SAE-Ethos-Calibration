"""Probe track: the external known-answer fixture (Fan, Murugan, Krishnan & Nagaraj 2026, arXiv:2609.16436).

Stages (each takes --mock so the whole path runs on a laptop):
  P1 synth_trials  : parameter sweep through the vLLM target at T=0; label = OBSERVED choice
  P2 extract       : final-prompt-token residual at candidate layers per trial          (GPU)
  P3 train         : z-score + L2 logistic, CV over (layer, C); held-out accuracy; unit steering vector
  P4 calibrate     : lambda sweep -> psychometric curve -> switching point; invert to hit targets (GPU)
  G9               : gates/g9_probe_external_fixture.py

INVARIANT: every forward pass and every steering injection goes through replay.hooks.teacher_forced_forward
— the same code path G1/G2/G4 validated. The probe direction is just another steering vector in the store.
Labels come from the observed choice, never from a median split of the parameter; the switching point is a
calibration TARGET, not a training label. Absolute Llama-3.3-70B numbers are REPORTED beside ours, never gated.
"""
