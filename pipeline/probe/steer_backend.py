"""Backends for the item 6 steering driver (probe.run_steering). One interface, two implementations:

  MockBackend    numpy only, offline: a synthetic first-token model whose logit difference depends on the item's
                 decision variable and on lambda x cos(v, w_true), w_true = the site's frozen probe_clean. Lets the whole
                 driver and analysis run end to end on a laptop with known answers.
  TorchBackend   the pod: nnsight for the exact readout (replay.hooks.last_logits_batch, the batch-gated path G4 and P4
                 use) and residual capture; a plain-HF forward hook with KV-cached generate() for the coherence and
                 sampled-agreement passes, checked against the nnsight path before use (path_check).

Interface:
  option_ids(task) -> (hi_ids, lo_ids)
  readout(msgs_list, layer, vec, lam) -> {"served": (P, m), "softmax": (P, m)}       (hi/lo ids set per task first)
  resid_readout(msgs_list, layers) -> ({L: H [B, d]}, readout dict)                   unsteered
  generate(msgs_list, layer, vec, lam, max_new, sample=None) -> [token id lists]      sample = {"seeds": [...]} at T 0.8 / top-p 0.95
  cont_nll(msgs_list, conts) -> [per-token NLL lists]                                  unsteered, teacher-forced
  first_token_class(task, ids) -> "high" | "low" | "other"
  decode(ids) -> str
  path_check(msgs_list, layer, vec, lams, tol) -> dict
"""
import numpy as np

from probe.steer_exact import readouts, T_SERVED, TOP_P

# first-token ids observed in run 2's native trials (all 1,368: one first token per option). The pod STOPs if the
# tokenizer disagrees.
EXPECTED_OPTION_IDS = {"lottery": ((99510,), (39316,)), "ultimatum": ((24040,), (137256,))}


# ====================================================================== mock
class MockBackend:
    """Vocab: 0 = high option, 1 = low option, 2..15 filler. Logit(high) - logit(low) = a * (x - x0) + gain * lam *
    cos(v, w_true) * (1 + noise) where x is the decision variable (n / safe for the lottery, n for the ultimatum); filler
    logits rise with |lam| so parseable mass falls at large strengths (mock incoherence above |lam| = 0.6)."""
    V = 16

    def __init__(self, vectors, sites, gain=6.0, seed=0):
        self.vecs = vectors; self.sites = sites; self.gain = gain
        self.rng = np.random.default_rng(seed); self.task = None
        self.w_true = {(t, L): vectors[f"{t}_L{L}_probe_clean"] for t, L in sites}

    def option_ids(self, task):
        self.task = task
        return (0,), (1,)

    def _x(self, msgs):
        import re
        s = msgs[-1]["content"]
        if "ULTIMATUM" in s:
            n = float(re.search(r"offer you (\d+)", s).group(1)); return (n - 4.0) / 1.5
        safe = float(re.search(r"Guaranteed (\d+)", s).group(1)); n = float(re.search(r"50% chance of (\d+)", s).group(1))
        return (n / safe - 1.75) * 6.0

    def _persona(self, msgs):
        s = msgs[-1]["content"]
        from probe.naturalness import PERSONAS
        for task, pairs in PERSONAS.items():
            for hi, lo in pairs:
                if s.startswith(hi):
                    return 1.0
                if s.startswith(lo):
                    return -1.0
        return 0.0

    def _logits(self, msgs, layer, vec, lam):
        out = np.zeros((len(msgs), self.V))
        for i, m in enumerate(msgs):
            task = "ultimatum" if "ULTIMATUM" in m[-1]["content"] else "lottery"
            shift = 0.0
            if vec is not None and lam != 0:
                w = self.w_true.get((task, layer))
                c = 0.0 if w is None else float(np.asarray(vec) @ w / (np.linalg.norm(vec) * np.linalg.norm(w) + 1e-12))
                shift = self.gain * lam * c
            d = self._x(m) + shift + 1.5 * self._persona(m)
            out[i, 0], out[i, 1] = d / 2, -d / 2
            out[i, 2:] = -6.0 + 9.0 * max(0.0, abs(lam) - 0.6)
        return out

    def readout(self, msgs, layer, vec, lam):
        hi, lo = self.option_ids(self.task or "lottery")
        return readouts(self._logits(msgs, layer, vec, lam), hi, lo)

    def resid_readout(self, msgs, layers):
        H = {}
        for L in layers:
            rows = []
            for m in msgs:
                task = "ultimatum" if "ULTIMATUM" in m[-1]["content"] else "lottery"
                w = self.w_true.get((task, L))
                base = np.random.default_rng(abs(hash(m[-1]["content"])) % 2 ** 32).normal(size=len(next(iter(self.vecs.values()))))
                rows.append(base + (0 if w is None else 3.0 * self._persona(m) * w))
            H[L] = np.stack(rows)
        return H, self.readout(msgs, None, None, 0.0)

    def generate(self, msgs, layer, vec, lam, max_new, sample=None):
        outs = []
        for i, m in enumerate(msgs):
            if sample is not None:
                lg = self._logits([m], layer, vec, lam)[0]
                p = np.exp((lg - lg.max()) / T_SERVED); p /= p.sum()
                r = np.random.default_rng(sample["seeds"][i])
                first = int(r.choice(self.V, p=p))
                outs.append([first, 3, 4][:max_new])
            else:
                r = np.random.default_rng(i)
                k = 1 + int(4 * max(0.0, abs(lam) - 0.6) * 10)         # repetitive at large |lam|
                base = list(r.integers(2, self.V, size=max(1, max_new // k)))
                outs.append((base * k)[:max_new] if abs(lam) > 0.6 else list(r.integers(2, self.V, size=max_new)))
        return outs

    def cont_nll(self, msgs, conts):
        return [[1.0 + (0.0 if len(set(c)) > len(c) // 3 else 1.2)] * len(c) for c in conts]

    def first_token_class(self, task, ids):
        return "high" if ids and ids[0] == 0 else "low" if ids and ids[0] == 1 else "other"

    def decode(self, ids):
        hi = "Accept" if self.task == "ultimatum" else "Risky Option"
        lo = "Reject" if self.task == "ultimatum" else "Safe Option"
        return {0: hi, 1: lo}.get(ids[0] if ids else -1, "Hmm")

    def path_check(self, msgs, layer, vec, lams, tol):
        return {"ok": True, "max_gap": 0.0, "tol": tol, "mock": True}

    def batch_gate(self, task, vec, layer, tol):
        return {"ok": True, "mock": True}


# ====================================================================== torch (pod)
def check_pins():
    """The replay profile's pinned library versions must be the installed ones (torch compared without its +cuda suffix)."""
    import importlib
    import modelcfg
    pins = (modelcfg.models().get("replay") or {}).get("pins") or {}
    bad = {}
    for lib, want in pins.items():
        got = importlib.import_module(lib).__version__.split("+")[0]
        if got != str(want):
            bad[lib] = {"want": str(want), "got": got}
    if bad:
        raise RuntimeError(f"STOP: library versions differ from the profile's replay.pins: {bad}")
    return pins


class TorchBackend:
    def __init__(self, batch_size=32):
        import modelcfg
        self.pins = check_pins()
        from replay.modelload import load_target
        self.lm = load_target("target")
        self.ser = modelcfg.serializer().apply_to_tokenizer
        self.bs = int(batch_size)
        self.hi = self.lo = None
        self.hf = getattr(self.lm.model, "_model", None) or self.lm.model
        self.hf_layers = modelcfg.decoder_layers(self.hf)

    # ---------------------------------------------------------------- helpers
    def _ids(self, msgs_list):
        return [self.ser(self.lm.tokenizer, m, add_generation_prompt=True) for m in msgs_list]

    def option_ids(self, task):
        from probe.tasks import TASKS
        tok = self.lm.tokenizer
        hi = (tok.encode(TASKS[task]["options"]["high"], add_special_tokens=False)[0],)
        lo = (tok.encode(TASKS[task]["options"]["low"], add_special_tokens=False)[0],)
        exp = EXPECTED_OPTION_IDS.get(task)
        if exp and (hi, lo) != exp:
            raise RuntimeError(f"STOP: option first-token ids {hi}/{lo} differ from run 2's observed {exp}")
        self.hi, self.lo = hi, lo
        return hi, lo

    def _readout_torch(self, lg):
        """GPU readouts, identical math to steer_exact.readouts (checked against it in path_check)."""
        import torch
        z = lg.double() / T_SERVED
        p = torch.softmax(z, -1)
        sp, si = torch.sort(p, descending=True, stable=True)
        keep_s = (torch.cumsum(sp, -1) - sp) < TOP_P
        keep = torch.zeros_like(keep_s).scatter(-1, si, keep_s)
        q = p * keep; q = q / q.sum(-1, keepdim=True)
        out = {}
        for name, dist in (("served", q), ("softmax", p)):
            h = dist[:, list(self.hi)].sum(-1); l = dist[:, list(self.lo)].sum(-1); m = h + l
            out[name] = ((h / m.clamp(min=1e-300)).cpu().numpy(), m.cpu().numpy())
        return out

    def readout(self, msgs_list, layer, vec, lam):
        from replay.hooks import last_logits_batch
        ids = self._ids(msgs_list)
        parts = []
        for i in range(0, len(ids), self.bs):
            steer = None if (vec is None or lam == 0) else (vec, lam)
            lg = last_logits_batch(self.lm, ids[i:i + self.bs], layer if steer else None, steer)
            parts.append(self._readout_torch(lg))
        return {k: (np.concatenate([p[k][0] for p in parts]), np.concatenate([p[k][1] for p in parts])) for k in ("served", "softmax")}

    def resid_readout(self, msgs_list, layers):
        import torch
        from replay.hooks import _left_pad, resid_post, _val
        import modelcfg
        envoys = modelcfg.decoder_layers(self.lm.model)
        ids = self._ids(msgs_list)
        H = {L: [] for L in layers}; parts = []
        for i in range(0, len(ids), self.bs):
            x, mask, pos = _left_pad(self.lm.tokenizer, ids[i:i + self.bs])
            saved = {}
            with torch.no_grad(), self.lm.model.trace({"input_ids": x, "attention_mask": mask, "position_ids": pos}):
                for L in sorted(layers):                                    # execution order
                    saved[L] = resid_post(envoys[L].output)[:, -1].float().save()
                lg = self.lm.model.output.logits[:, -1].float().save()
            for L in layers:
                H[L].append(_val(saved[L]).cpu().numpy())
            parts.append(self._readout_torch(_val(lg)))
        ro = {k: (np.concatenate([p[k][0] for p in parts]), np.concatenate([p[k][1] for p in parts])) for k in ("served", "softmax")}
        return {L: np.concatenate(H[L]) for L in layers}, ro

    def _hook(self, layer, vec, lam, mask):
        """Forward hook on the HF decoder block: prefill adds lam * (row mean residual norm over real tokens, first real
        token excluded) * unit(vec) at real positions and stores the norms; decode steps reuse them."""
        import torch
        state = {}
        v = torch.as_tensor(np.asarray(vec, dtype=np.float32))

        def fn(module, args, output):
            hs = output[0] if isinstance(output, (tuple, list)) else output
            unit = (v / (v.norm() + 1e-6)).to(hs.device, hs.dtype)
            if hs.shape[1] > 1:
                m = mask.to(hs.device).bool()
                first = (mask.cumsum(-1) == 1).to(hs.device)
                use = m & ~first
                norms = hs.float().norm(dim=-1)
                state["norm"] = ((norms * use).sum(-1) / use.sum(-1).clamp(min=1)).to(hs.dtype)
                add = (float(lam) * state["norm"])[:, None, None] * unit[None, None, :] * m[:, :, None].to(hs.dtype)
            else:
                add = (float(lam) * state["norm"])[:, None, None] * unit[None, None, :]
            hs2 = hs + add
            return (hs2,) + tuple(output[1:]) if isinstance(output, (tuple, list)) else hs2
        return self.hf_layers[int(layer)].register_forward_hook(fn)

    def _hf_last_logits(self, ids_list, layer, vec, lam):
        import torch
        from replay.hooks import _left_pad
        x, mask, pos = _left_pad(self.lm.tokenizer, ids_list)
        dev = next(self.hf.parameters()).device
        h = self._hook(layer, vec, lam, mask) if (vec is not None and lam != 0) else None
        try:
            with torch.no_grad():
                out = self.hf(input_ids=x.to(dev), attention_mask=mask.to(dev), position_ids=pos.to(dev))
        finally:
            if h is not None:
                h.remove()
        return out.logits[:, -1].float()

    def path_check(self, msgs_list, layer, vec, lams, tol):
        """The HF-hook path (used for generation) must equal the nnsight path at prefill, and the GPU readout must equal
        the numpy readout of steer_exact, before either is used."""
        import torch
        from replay.hooks import last_logits_batch
        ids = self._ids(msgs_list)
        rep = {"tol": tol, "lams": list(lams), "gaps": {}, "readout_gap": None}
        ok = True
        for lam in lams:
            a = torch.log_softmax(self._hf_last_logits(ids, layer, vec, lam).cpu(), -1)
            b = torch.log_softmax(last_logits_batch(self.lm, ids, layer if lam else None, (vec, lam) if lam else None).float().cpu(), -1)
            top = b.topk(20, dim=-1).indices
            gap = float((a.gather(1, top) - b.gather(1, top)).abs().max())
            rep["gaps"][str(lam)] = gap; ok = ok and gap <= tol and bool((a.argmax(-1) == b.argmax(-1)).all())
        lg = last_logits_batch(self.lm, ids, None, None).float()
        g = self._readout_torch(lg); n = readouts(lg.cpu().numpy(), self.hi, self.lo)
        rgap = max(float(np.nanmax(np.abs(g[k][0] - n[k][0]))) for k in ("served", "softmax"))
        rep["readout_gap"] = rgap; ok = ok and rgap <= 1e-6
        rep["ok"] = ok
        return rep

    def generate(self, msgs_list, layer, vec, lam, max_new, sample=None):
        """KV-cached decoding written out (prefill, then one token per step with past_key_values), not hf.generate():
        transformers 5.x's generate() rejects attention_mask for this class (pod, 2026-10-05). Greedy, or per-row seeded
        sampling at T 0.8 / top-p 0.95 (replay.hooks.sample_from_logits, seed = seeds[row] * 104729 + step). Steering via
        the forward hook (prefill norm held fixed in decode). Checked against an uncached recompute in the 4B smoke."""
        import torch
        from replay.hooks import _left_pad, sample_from_logits, _stops
        ids = self._ids(msgs_list)
        outs = []
        dev = next(self.hf.parameters()).device
        stops = set(_stops())
        for i in range(0, len(ids), self.bs):
            chunk = ids[i:i + self.bs]
            seeds = sample["seeds"][i:i + self.bs] if sample is not None else None
            x, mask, pos = _left_pad(self.lm.tokenizer, chunk)
            x, mask, pos = x.to(dev), mask.to(dev), pos.to(dev)
            h = self._hook(layer, vec, lam, mask) if (vec is not None and lam != 0) else None
            B = x.shape[0]; gen = [[] for _ in range(B)]; done = [False] * B
            try:
                with torch.no_grad():
                    out = self.hf(input_ids=x, attention_mask=mask, position_ids=pos, use_cache=True)
                    pkv, lg = out.past_key_values, out.logits[:, -1].float()
                    cur = pos[:, -1].clone()
                    for step in range(max_new):
                        nxt = []
                        for r in range(B):
                            t = int(lg[r].argmax()) if seeds is None else sample_from_logits(lg[r], T_SERVED, TOP_P, seeds[r] * 104729 + step)
                            nxt.append(t)
                            if not done[r]:
                                if t in stops:
                                    done[r] = True
                                else:
                                    gen[r].append(t)
                        if all(done) or step == max_new - 1:
                            break
                        mask = torch.cat([mask, torch.ones((B, 1), dtype=mask.dtype, device=dev)], 1)
                        cur = cur + 1
                        out = self.hf(input_ids=torch.tensor(nxt, device=dev)[:, None], attention_mask=mask,
                                      position_ids=cur[:, None], past_key_values=pkv, use_cache=True)
                        pkv, lg = out.past_key_values, out.logits[:, -1].float()
            finally:
                if h is not None:
                    h.remove()
            outs += gen
        return outs

    def generate_uncached(self, msgs_list, layer, vec, lam, max_new):
        """Greedy reference without a cache: the full sequence re-run every step through _hf_last_logits (whose prefill
        equals the nnsight path, path_check). Smoke-only: proves the cached loop."""
        ids = self._ids(msgs_list)
        outs = []
        from replay.hooks import _stops
        stops = set(_stops())
        for p in ids:
            seq, g = list(p), []
            for _ in range(max_new):
                t = int(self._hf_last_logits([seq], layer, vec, lam)[0].argmax())
                if t in stops:
                    break
                g.append(t); seq.append(t)
            outs.append(g)
        return outs

    def cont_nll(self, msgs_list, conts):
        import torch
        from replay.hooks import _left_pad
        ids = self._ids(msgs_list)
        dev = next(self.hf.parameters()).device
        out = []
        for i in range(0, len(ids), self.bs):
            full = [p + c for p, c in zip(ids[i:i + self.bs], conts[i:i + self.bs])]
            x, mask, pos = _left_pad(self.lm.tokenizer, full)
            with torch.no_grad():
                lg = self.hf(input_ids=x.to(dev), attention_mask=mask.to(dev), position_ids=pos.to(dev)).logits.float()
            lp = torch.log_softmax(lg, -1)
            T = x.shape[1]
            for r, (p, c) in enumerate(zip(ids[i:i + self.bs], conts[i:i + self.bs])):
                start = T - len(c)
                out.append([float(-lp[r, j - 1, x[r, j]]) for j in range(start, T)])
        return out

    def first_token_class(self, task, ids):
        return "high" if ids and ids[0] in self.hi else "low" if ids and ids[0] in self.lo else "other"

    def decode(self, ids):
        return self.lm.tokenizer.decode(ids)

    def batch_gate(self, task, vec, layer, tol):
        from probe.batch_gate import run_gate
        return run_gate(self.lm, {f"probe_{task}": vec, f"probe_{task}__layer": np.array(layer)}, task, tol)
