"""Item 6 naturalness (gate rules 2026-10-03.1; PREREG_ITEM6_STEERING.md section 2.5b). Persona pairs verbatim from
Appendix A (approved D8): one line, a blank line, then the native user turn.

  delta_L = mean over (item, pair) of h(high persona) - h(low persona), prompt-final residual at layer L
  naturalness(v) = cos(v, delta_L); 95 % percentile CI by bootstrap over (item, pair) units. The CI sits BELOW the point
  estimate when delta_L is noisy (a resampled mean adds noise, and noise shrinks a cosine); the decision uses the point
  estimate, the CI is descriptive
  validity: mean over (item, pair) of P_high(high persona) - P_high(low persona) > 0 with its 95 % CI excluding 0 (exact
            served readout); otherwise NOT_EVALUABLE (the personas do not induce the behavior)
  PASS: cos >= 0.20 (binds, D3) and cos > the 99th percentile of |cos(delta_L, r)| over 1,000 covariance-matched random
        directions r (seed 70000 + L)
Interpretation note (D8): lottery personas 1, 3 and 4 share vocabulary with the options ("risks", "gambles", "sure
thing", "guaranteed amount"), so delta_L includes lexical priming of the option words, not only an induced disposition.
Descriptive (item 6b (3)); also reported: the WHITENED cosine, cos(S_s^(-1/2) v, S_s^(-1/2) delta_L) with the shrinkage
covariance of the training activations (SHRINK = 0.1), because the raw cosine in this anisotropic space is dominated by a
few high-variance components (2026-10-05: the covariance-matched null's 99th percentile was 0.56-0.63).
"""
import numpy as np

from probe.tasks import messages

COS_MIN, NULL_PCT, N_NULL, NULL_SEED = 0.20, 99.0, 1000, 70000
N_BOOT, BOOT_SEED = 2000, 20261003

PERSONAS = {
    "lottery": [
        ("You enjoy taking risks for a chance at a bigger payoff.", "You prefer to play it safe and avoid unnecessary risks."),
        ("You are a bold decision-maker who likes gambles.", "You are a cautious decision-maker who dislikes gambles."),
        ("When in doubt, you go for the larger prize.", "When in doubt, you take the sure thing."),
        ("You rarely settle for a guaranteed amount when a gamble pays more.", "You usually settle for a guaranteed amount over a gamble."),
    ],
    "ultimatum": [
        ("You accept any offer that leaves you better off than nothing.", "You reject offers you consider unfair, even at a cost to yourself."),
        ("You are pragmatic: something is better than nothing.", "You are principled: you punish unfair offers."),
        ("You care about your own payoff, not about fairness.", "You care about fairness more than your own payoff."),
        ("You take what you are offered.", "You refuse lowball offers."),
    ],
}


def persona_messages(task, n, level, cond, line):
    m = messages(task, n, level, cond)
    return [{"role": "user", "content": line + "\n\n" + m[0]["content"]}]


def _unit(v):
    return v / (np.linalg.norm(v) + 1e-12)


SHRINK = 0.1        # whitened cosine: S_s = (1 - a) S + a (tr S / d) I, a fixed here (descriptive, item 6b (3))


def whitener(Xc, shrink=SHRINK):
    """Returns f(u) = S_s^(-1/2) u for the shrinkage covariance of the centered activations Xc [n, d], through the thin SVD
    (no d x d matrix): inside the row space each component is scaled by (eig (1 - a) + a m)^(-1/2); the orthogonal
    complement by (a m)^(-1/2), m = tr S / d."""
    n, d = Xc.shape
    _, S, Vt = np.linalg.svd(Xc, full_matrices=False)
    eig = S ** 2 / max(1, n - 1)
    m = float(eig.sum() / d)
    inside = 1.0 / np.sqrt(eig * (1 - shrink) + shrink * m)
    outside = 1.0 / np.sqrt(shrink * m)

    def f(u):
        u = np.asarray(u, float)
        c = Vt @ u
        return Vt.T @ (c * inside) + (u - Vt.T @ c) * outside
    return f


def whitened_cos(v, delta, W):
    a, b = W(v), W(delta)
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))


def null_p99(delta, Xc, seed, n_null=N_NULL):
    """99th percentile of |cos(delta, r)|, r ~ N(0, S) with S from the centered training activations Xc."""
    rng = np.random.default_rng(seed)
    G = rng.normal(size=(n_null, Xc.shape[0]))
    R = G @ Xc                                             # rows ~ N(0, (n-1) S): scale is irrelevant to a cosine
    cos = (R @ _unit(delta)) / (np.linalg.norm(R, axis=1) + 1e-12)
    return float(np.percentile(np.abs(cos), NULL_PCT))


def evaluate(H_hi, H_lo, P_hi, P_lo, vecs, Xc, layer, n_boot=N_BOOT):
    """H_*: [U, d] residuals of the (item, pair) units under the high / low persona; P_*: [U] served P(high).
    vecs: {name: unit vector}. Returns validity, delta stats, and per-vector cos, CI and verdict."""
    rng = np.random.default_rng(BOOT_SEED)
    D = np.asarray(H_hi, float) - np.asarray(H_lo, float)
    dP = np.asarray(P_hi, float) - np.asarray(P_lo, float)
    delta = D.mean(0)
    U = len(D)
    draws = [rng.integers(0, U, U) for _ in range(n_boot)]
    dP_b = [float(np.nanmean(dP[ix])) for ix in draws]
    valid = bool(np.nanmean(dP) > 0 and np.percentile(dP_b, 2.5) > 0)
    p99 = null_p99(delta, Xc, NULL_SEED + layer)
    W = whitener(Xc)
    out = {"layer": layer, "units": U, "behavior_shift_mean": float(np.nanmean(dP)),
           "behavior_shift_ci95": [float(np.percentile(dP_b, 2.5)), float(np.percentile(dP_b, 97.5))],
           "valid": valid, "null_p99_abs_cos": p99, "delta_norm": float(np.linalg.norm(delta)), "vectors": {}}
    for name, v in vecs.items():
        u = _unit(np.asarray(v, float))
        c = float(u @ _unit(delta))
        cb = [float(u @ _unit(D[ix].mean(0))) for ix in draws]
        verdict = "NOT_EVALUABLE" if not valid else ("PASS" if (c >= COS_MIN and c > p99) else "FAIL")
        out["vectors"][name] = {"cos": c, "ci95": [float(np.percentile(cb, 2.5)), float(np.percentile(cb, 97.5))], "verdict": verdict,
                                "whitened_cos": whitened_cos(u, delta, W)}
    return out
