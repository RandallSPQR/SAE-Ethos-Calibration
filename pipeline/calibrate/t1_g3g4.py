#!/usr/bin/env python3
"""Revised G3/G4 statistics (2026-09-16), run on the same loaded model/SAE. Rewrites
features/known_answer_report.json and features/steering_report.json, then runs gates G3,G4.

G3: a labeled SAE feature is SPARSE (fires on a few tokens of its concept), so a per-position AUROC
    against zeros is the wrong test. Discrimination is scored on WINDOW-MAX activations (16-token
    windows) plus fraction-active; raw per-position arrays are kept for the record.
G4: the readout must sit at a live decision point. Prompt A asks for code (Gemma is undecided between
    a prose preface and a code block as its first token); prompt B is the original "Write something."
    kept for the record. Readout = P(first generated token opens code). Greedy samples per strength.
"""
import json
import sys
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from calibrate.t1_ladder import CODE_TEXT, PROSE_TEXT, MENTION_TEXT, CAL, CFG, _dump, _last_pos_mass  # noqa: E402

WINDOW = 16
PROMPT_A = [{"role": "user", "content": "Show me how to compute the nth Fibonacci number."}]
PROMPT_B = [{"role": "user", "content": "Write something."}]


def window_max(a, w=WINDOW):
    a = np.asarray(a)
    return [float(a[i:i + w].max()) for i in range(0, len(a), w) if len(a[i:i + w]) >= w // 2]


def main(out="/workspace/t1"):
    import torch
    from model_io.gemma2 import apply_to_tokenizer
    from replay.modelload import load_target
    from replay.hooks import teacher_forced_forward, greedy_generate_at_layer
    from replay.sae import load_sae, encode_dense
    from gates.g3_feature_known_answer import auroc
    feat = Path(out) / "features"
    lm = load_target("target"); tok = lm.tokenizer; sae = load_sae()
    fidx = CAL["code_feature_index"]
    feats = [fidx] + list(CAL.get("extra_feature_indices", []) or [])

    def acts(text, fx):
        ids = tok(text, add_special_tokens=True)["input_ids"]
        f = teacher_forced_forward(lm, None, capture_residual=True, input_ids=ids)
        return encode_dense(sae, f.residual)[1:, fx]

    # ---- G3
    per = {}
    for fx in feats:
        c, p, m = acts(CODE_TEXT, fx), acts(PROSE_TEXT, fx), acts(MENTION_TEXT, fx)
        pl = acts(PROSE_TEXT + "\n" + CODE_TEXT, fx)
        cw, pw, mw, plw = window_max(c), window_max(p), window_max(m), window_max(pl)
        per[str(fx)] = {"auroc_window_max": auroc(cw, pw), "auroc_position": auroc(c.tolist(), p.tolist()),
                        "frac_active": {"code": float((c > 0).mean()), "prose": float((p > 0).mean()), "mention": float((m > 0).mean())},
                        "window_max": {"code": cw, "prose": pw, "mention": mw, "planted": plw},
                        "max_act": {"code": float(c.max()), "prose": float(p.max()), "mention": float(m.max())}}
        print(f"G3 feature {fx}: auroc_window_max={per[str(fx)]['auroc_window_max']:.3f} auroc_position={per[str(fx)]['auroc_position']:.3f} "
              f"active code/prose/mention={per[str(fx)]['frac_active']}")
    main_f = per[str(fidx)]
    rep3 = {"feature": fidx, "statistic": f"window-max ({WINDOW} tokens) AUROC; per-position arrays under *_raw",
            "concept_positions": main_f["window_max"]["code"], "other_positions": main_f["window_max"]["prose"],
            "concept_positions_raw": acts(CODE_TEXT, fidx).tolist(), "other_positions_raw": acts(PROSE_TEXT, fidx).tolist(),
            "planted_secrecy_activation": float(np.mean(main_f["window_max"]["planted"])),
            "baseline_secrecy_activation": float(np.mean(main_f["window_max"]["prose"])),
            "mention_behavior_activation": float(np.mean(main_f["window_max"]["mention"])),
            "control_behavior_activation": float(np.mean(main_f["window_max"]["prose"])),
            "auroc": main_f["auroc_window_max"], "frac_active": main_f["frac_active"], "per_feature": per,
            "note": "planted/mention fields reuse the code feature (code planted in prose; prose that talks about code); "
                    "first run (per-position AUROC 0.578) kept in git history"}
    _dump(feat / "known_answer_report.json", rep3)

    # ---- G4
    W = sae.W_dec[fidx].detach().float().cpu().numpy()
    openers = ["```", "def", "import", "class", "#", "print", "Here", "Sure", "Certainly"]
    opener_ids = sorted({tok(t, add_special_tokens=False)["input_ids"][0] for t in ["```", "def", "import", "class", "#", "print"]})
    sweep = yaml.safe_load((CFG / "run.yaml").read_text())["steering"]["sweep"]
    curves, samples = {}, {}
    for name, prompt in (("A_code_request", PROMPT_A), ("B_write_something", PROMPT_B)):
        ids = apply_to_tokenizer(tok, prompt, add_generation_prompt=True)
        curves[name], samples[name] = {}, {}
        for s in sweep:
            curves[name][str(s)] = _last_pos_mass(lm, ids, (W, s), opener_ids)
            samples[name][str(s)] = tok.decode(greedy_generate_at_layer(lm, ids, lm.layer, (W, s), max_new_tokens=20))
        print(f"G4 {name}: " + " ".join(f"{k}:{v:.3f}" for k, v in curves[name].items()))
        for s in sweep:
            print(f"    {s:+.1f} -> {samples[name][str(s)][:70]!r}")
    _dump(feat / "steering_report.json", {"feature": fidx, "readout": "P(first generated token in code openers)",
                                          "opener_ids": opener_ids, "prompt": PROMPT_A[0]["content"],
                                          "curve": curves["A_code_request"], "curves_all": curves, "samples": samples,
                                          "note": "first run used prompt B only (P~1e-7 floor, monotone 1000x rise); kept in git history"})
    import subprocess
    subprocess.run([sys.executable, "-m", "gates.run_gates", "--features", str(feat), "--transcripts", str(Path(out) / "transcripts"),
                    "--replayed", str(Path(out) / "replayed"), "--gates", "G3,G4"], cwd=ROOT)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/workspace/t1")
