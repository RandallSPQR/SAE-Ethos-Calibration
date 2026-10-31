"""Item 6b CAA vectors (gate rules 2026-10-06.1; PREREG_ITEM6B_STEERING.md sections 3.1-3.2, 5). Built on the pod, from
the 630 training prompts (safe 30 / 50 / 100), hashed and logged before any steered output.

  D       counterbalanced A/B: each training prompt in the A/B format (training letters, exactly half Safe = A); teacher-
          force "A" and "B"; block-L residual at the answer token; mean of [x(risky letter) - x(safe letter)]
  D_word  native format; teacher-force "Safe Option" and "Risky Option"; residual at their first token; same contrast
Both: the answer tokens' embedding and unembedding difference directions projected out (Gram-Schmidt), unit-normalized.
sd_v: std of X_train @ D over the run's native prompt-final training activations (the 6b strength unit).
On-manifold STOP (amendment 3): D's worst-dimension push of a 1-sd step > ISO_RANGE_MAX.
"""
import hashlib

import numpy as np

from probe.tasks import ab_messages, assign_letters, item_key, messages, UNITS, LETTER_SEED_TRAIN, TASKS

TRAIN_LEVELS = (30, 50, 100)
ISO_RANGE_MAX = 2.93


def training_items():
    return [{"n": n, "level": lv, "cell": f"{o}/{u}", "cond": {"unit": u, "order": o}}
            for lv in TRAIN_LEVELS for n in TASKS["lottery"]["grid"] for o in ("safe_first", "risky_first") for u in UNITS]


def _u(v):
    return v / (np.linalg.norm(v) + 1e-12)


def project_out(v, dirs):
    basis = []
    for d in dirs:
        u = np.asarray(d, float).copy()
        for q in basis:
            u -= (u @ q) * q
        n = np.linalg.norm(u)
        if n > 1e-8:
            basis.append(u / n)
    w = np.asarray(v, float).copy()
    for q in basis:
        w -= (w @ q) * q
    return w, basis


def build(be, layer, fmt, Xt, n_items=None):
    """fmt 'ab' -> D, 'word' -> D_word. Returns (unit vector float32, info). n_items: smoke only (the pod uses all 630)."""
    its = training_items()
    if fmt == "ab":
        letters = assign_letters(its, LETTER_SEED_TRAIN)
        msgs = [ab_messages(it["n"], it["level"], it["cond"], letters[item_key(it)]) for it in its]
        risky_ans = ["B" if letters[item_key(it)] == "A" else "A" for it in its]
        safe_ans = [letters[item_key(it)] for it in its]
        a_ids, b_ids = be.letter_ids()
        tok_hi, tok_lo = a_ids[0], b_ids[0]
        n_safe_a = sum(1 for it in its if letters[item_key(it)] == "A")
        if n_items:
            msgs, risky_ans, safe_ans = msgs[:n_items], risky_ans[:n_items], safe_ans[:n_items]
    else:
        msgs = [messages("lottery", it["n"], it["level"], it["cond"]) for it in its]
        risky_ans = [TASKS["lottery"]["options"]["high"]] * len(its)
        safe_ans = [TASKS["lottery"]["options"]["low"]] * len(its)
        hi, lo = be.option_ids("lottery")
        tok_hi, tok_lo = hi[0], lo[0]
        n_safe_a = None
        if n_items:
            msgs, risky_ans, safe_ans = msgs[:n_items], risky_ans[:n_items], safe_ans[:n_items]
    xr = be.answer_resid(msgs, risky_ans, layer)
    xs = be.answer_resid(msgs, safe_ans, layer)
    raw = (xr - xs).mean(0)
    E, U = be.token_rows([tok_hi, tok_lo])
    dirs = [E[tok_hi] - E[tok_lo], U[tok_hi] - U[tok_lo]]
    proj, basis = project_out(raw, dirs)
    v = _u(proj).astype(np.float32)
    s = float((Xt @ _u(v.astype(np.float64))).std())
    sd = Xt.std(0) + 1e-9; low = np.argsort(sd)[: len(sd) // 10]
    vv = _u(v.astype(np.float64))
    info = {"fmt": fmt, "n_prompts": len(msgs), "n_safe_is_A": n_safe_a, "raw_norm": float(np.linalg.norm(raw)),
            "cos_raw_with_token_dirs": [float(_u(raw) @ _u(d)) for d in dirs], "removed_share": float(1 - np.linalg.norm(proj) ** 2 / (np.linalg.norm(raw) ** 2 + 1e-30)),
            "sd_v": s, "max_dim_push_1sd": float(np.max(s * np.abs(vv) / sd)), "low_variance_share": float(np.sum(vv[low] ** 2)),
            "sha256": hashlib.sha256(np.ascontiguousarray(v, dtype="<f4").tobytes()).hexdigest()}
    return v, info


def on_manifold_stop(info, iso_max=ISO_RANGE_MAX):
    return info["max_dim_push_1sd"] > iso_max


def diagnose(v, run_dir, probe_clean=None, layer=38, n_null=1000, seed=90038):
    """Descriptive cosines of a CAA vector with the item-6 reference directions, raw and whitened (shrinkage 0.1), with
    the whitened covariance-matched null (99th percentile) per reference (as probe.whitened_check)."""
    from probe import vectors as VE
    from probe.naturalness import whitener
    z = np.load(f"{run_dir}/probe/lottery/activations.npz")
    X = z[f"X_{layer}"].astype(np.float64); y = z["y"].astype(int)
    tr = VE.training_split("lottery", z); Xt = X[tr]; Xc = Xt - Xt.mean(0)
    refs = {}
    for region in ("below", "above"):
        d, _ = VE.n_direction_homogeneous(X, z, y, "lottery", run_dir, region)
        if d is not None:
            refs[f"n_homog_{region}"] = d
    lv, par = z["level"].astype(float)[tr], z["param"].astype(float)[tr]
    Xw, nw = Xt.copy(), par.copy()
    for l in np.unique(lv):
        m = lv == l; Xw[m] -= Xw[m].mean(0); nw[m] -= nw[m].mean()
    refs["n_slope_all"] = _u(Xw.T @ nw)
    refs["mod_frame_matched"] = _u(VE.mod_frame_matched(X, z, y, tr)[0])
    if probe_clean is not None:
        refs["probe_clean"] = _u(np.asarray(probe_clean, float))
    W = whitener(Xc); vv = _u(np.asarray(v, float)); Wv = _u(W(vv))
    R = np.random.default_rng(seed).normal(size=(n_null, Xc.shape[0])) @ Xc
    WR = np.stack([_u(W(r)) for r in R])
    out = {}
    for k, r in refs.items():
        Wr = _u(W(r))
        out[k] = {"cos": float(vv @ _u(r)), "whitened_cos": float(Wv @ Wr), "whitened_null_p99": float(np.percentile(np.abs(WR @ Wr), 99))}
    return out
