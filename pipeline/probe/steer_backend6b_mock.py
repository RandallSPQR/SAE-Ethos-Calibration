"""Mock backend for the item 6b driver (offline, numpy). A synthetic model that knows both answer formats.

Vocab: 0 = "Risky", 1 = "Safe", 2..15 filler, 16 = "A", 17 = "B", 20 = a correct manipulation answer, 21 = garbage.
- Choice: logit difference (risky - safe) = (n - sp_level) x 6 / safe (sp_level: run 2's served switching points) + GAIN x (strength / sd_w) x cos(v, w_true) in absolute
  mode (sd_w = the natural sd of w_true); filler logits rise once |k| > 2.5 (note_k), so k = +-4 is incoherent.
- answer_resid: base(prompt) + 2 w_true (risky answer) or -2 w_true (safe answer) + 3 u_letter (answer "A") or -3 u_letter
  ("B"); token_rows gives E[A] - E[B] = U[A] - U[B] = u_letter. So the counterbalanced CAA recovers w_true and the
  projection removes the letter direction.
- Manipulation prompts: a correct statement unless |k| > 2.5; coherence prompts: varied text unless |k| > 2.5.
"""
import re
import zlib

import numpy as np

from probe.steer_exact import readouts, T_SERVED

A_ID, B_ID, GOOD, BAD = 16, 17, 20, 21


class Mock6bBackend:
    V = 24
    GAIN = 2.5
    SP = {30.0: 63.9, 50.0: 88.5, 70.0: 100.3, 100.0: 119.2}     # run 2's served switching points by safe level

    def __init__(self, w_true, sd_w, seed=0):
        self.w = np.asarray(w_true, float) / np.linalg.norm(w_true); self.sd_w = float(sd_w); self.d = len(w_true)
        rng = np.random.default_rng(seed + 1)
        u = rng.normal(size=self.d); u -= (u @ self.w) * self.w
        self.u_letter = u / np.linalg.norm(u)
        self.absolute = False; self.k = 0.0
        self.hi, self.lo = (0,), (1,)

    # ---- interface
    def note_k(self, k):
        self.k = float(k)

    def option_ids(self, task):
        self.hi, self.lo = (0,), (1,)
        return self.hi, self.lo

    def letter_ids(self):
        return (A_ID,), (B_ID,)

    def set_ids(self, hi, lo):
        self.hi, self.lo = tuple(hi), tuple(lo)

    def _shift(self, vec, lam):
        if vec is None or lam == 0:
            return 0.0
        v = np.asarray(vec, float)
        c = float(v @ self.w / (np.linalg.norm(v) + 1e-12))
        return self.GAIN * (lam / self.sd_w if self.absolute else lam * 10) * c

    @staticmethod
    def _parse(s):
        safe = float(re.search(r"Guaranteed (\d+)", s).group(1)); n = float(re.search(r"50% chance of (\d+)", s).group(1))
        m = re.search(r"Option ([AB]): Guaranteed", s)
        return safe, n, (m.group(1) if m else None)

    def _logits(self, msgs, layer, vec, lam):
        out = np.full((len(msgs), self.V), -30.0)
        for i, m in enumerate(msgs):
            safe, n, safe_letter = self._parse(m[-1]["content"])
            d = (n - self.SP.get(safe, 1.43 * safe)) * 6.0 / safe + self._shift(vec, lam)
            if safe_letter:
                rk = B_ID if safe_letter == "A" else A_ID; sk = A_ID if safe_letter == "A" else B_ID
                out[i, rk], out[i, sk] = d / 2, -d / 2
            else:
                out[i, 0], out[i, 1] = d / 2, -d / 2
            out[i, 2:16] = -8.0 + 6.0 * max(0.0, abs(self.k) - 2.5)
        return out

    def readout(self, msgs, layer, vec, lam):
        return readouts(self._logits(msgs, layer, vec, lam), self.hi, self.lo)

    def generate(self, msgs, layer, vec, lam, max_new, sample=None):
        outs, bad = [], abs(self.k) > 2.5
        for i, m in enumerate(msgs):
            s = m[-1]["content"]
            if sample is not None:
                lg = self._logits([m], layer, vec, lam)[0]
                p = np.exp((lg - lg.max()) / T_SERVED); p /= p.sum()
                outs.append([int(np.random.default_rng(sample["seeds"][i]).choice(self.V, p=p)), 3][:max_new])
            elif "state the guaranteed amount" in s:
                outs.append([BAD] * 4 if bad else [GOOD])
            else:
                r = np.random.default_rng(i)
                outs.append([5] * max_new if bad else list(r.integers(2, 16, size=max_new)))
        return outs

    def cont_nll(self, msgs, conts):
        return [[1.0 + (1.2 if len(set(c)) <= 2 else 0.0)] * len(c) for c in conts]

    def first_token_class(self, task, ids):
        return "high" if ids and ids[0] == 0 else "low" if ids and ids[0] == 1 else "other"

    def decode(self, ids):
        t = ids[0] if ids else -1
        return {0: "Risky Option", 1: "Safe Option", A_ID: "A", B_ID: "B",
                GOOD: "The guaranteed amount is 70 tokens and the chance of winning is 50%.", BAD: "zzz zzz"}.get(t, "word")

    def answer_resid(self, msgs, answers, layer):
        rows = []
        for m, a in zip(msgs, answers):
            s = m[-1]["content"]
            base = np.random.default_rng(zlib.crc32(s.encode())).normal(size=self.d)
            _, _, safe_letter = self._parse(s)
            if safe_letter:
                risky = a != safe_letter
                letter = 1.0 if a == "A" else -1.0
            else:
                risky = a.startswith("Risky"); letter = 0.0
            rows.append(base + (2.0 if risky else -2.0) * self.w + 3.0 * letter * self.u_letter)
        return np.stack(rows)

    def token_rows(self, ids):
        E = {i: (self.u_letter / 2 if i == A_ID else -self.u_letter / 2 if i == B_ID else np.zeros(self.d)) for i in ids}
        return E, dict(E)

    def path_check(self, msgs, layer, vec, lams, tol):
        return {"ok": True, "mock": True}

    def batch_gate(self, task, vec, layer, tol):
        return {"ok": True, "mock": True}
