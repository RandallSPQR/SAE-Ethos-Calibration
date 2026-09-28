"""Instrument checks on REAL T3 spans (pre-registration 2026-09-28, "instrument checks first, on this data"):
G2 (SAE health + decoy hooks + JumpReLU integrity on real assistant spans, identity block carried from T1),
G3 (the known code feature on real spans: tool-block code positions vs reasoning-prose positions, window-max
AUROC) and G5 (oracle paired-only on real-span residuals captured BEFORE the LoRA loads). Each writes the JSON
its gate reads under features/. Samples are collected by replay.replay --instrument-sample N."""
import json
import re
from pathlib import Path

import numpy as np

CODE_MARKS = ("def ", "import ", "return ", "class ", "print(", "assert ")
CODE_TALK = ("def", "function", "import", "code", "python", "edit", "test", "assert")
WINDOW = 16


def _window_max(a, w=WINDOW):
    a = list(a)
    return [max(a[i:i + w]) for i in range(0, len(a), w) if len(a[i:i + w]) >= w // 2]


def token_regions(tok, span_ids):
    """Per span token: 'block' (inside a ```tool fence), 'prose' (reasoning before/after), from char offsets of
    the incrementally decoded span."""
    text = tok.decode(span_ids)
    m = re.search(r"```tool(.*?)```", text, re.S)
    if not m:
        return ["prose"] * len(span_ids), text, ""
    b0, b1 = m.start(), m.end()
    regions, cum = [], ""
    for k in range(len(span_ids)):
        cum = tok.decode(span_ids[: k + 1])
        pos = len(cum) - 1
        regions.append("block" if b0 <= pos < b1 else "prose")
    return regions, text, m.group(1)


def build_sae_health(lm, sae, samples, out_path, identity_from=None):
    """G2 on real spans. samples: [{'resid': [span,d], 'extra': {reader: [span,d]}, ...}]. Pools the assistant-span
    residuals (BOS never inside a span), decoys + scaled copies as the T1 ladder did, JumpReLU integrity, artifact
    identity; the TransformerLens tensor-identity block is data-independent and is CARRIED from the T1 report
    (same pinned artifact and hook), labeled as such."""
    from .sae import sae_health, hook_identification_report, jumprelu_integrity, sae_artifact_identity, sae_cfg
    from .modelload import hook_reader
    s = sae_cfg()
    chosen_name = s["hook_point"]
    readers = {h: hook_reader(h) for h in [chosen_name] + list(s.get("hook_candidates", []))}
    pooled = {h: [] for h in readers}
    per_l0, per_ve = [], []
    for smp in samples:
        for h, r in readers.items():
            arr = smp["resid"] if r == "block_output" else smp["extra"].get(r)
            if arr is not None:
                pooled[h].append(np.asarray(arr, dtype=np.float32))
        hd = sae_health(sae, smp["resid"], skip_bos=False)
        per_l0.append(hd["l0"]); per_ve.append(hd["var_explained"])
    res_by_hook = {h: np.concatenate(v, 0) for h, v in pooled.items() if v}
    for sc in (0.8, 1.2):
        res_by_hook[f"{chosen_name}_x{sc}"] = res_by_hook[chosen_name] * sc
    rep = hook_identification_report(lm, sae, res_by_hook, out_path, skip_bos=False,
                                     extra_candidates=[f"{chosen_name}_x0.8", f"{chosen_name}_x1.2"])
    jr = jumprelu_integrity(sae, res_by_hook[chosen_name])
    rep.update({"per_doc_l0": per_l0, "per_doc_var_explained": per_ve, "n_docs": len(samples), "ctx": "assistant span",
                "bos_excluded": True, "jumprelu_below_threshold_frac": jr["below_threshold_frac"], "jumprelu_n_active": jr["n_active"],
                "artifact": sae_artifact_identity(sae), "data": "real T3 assistant spans (decision turns)"})
    if identity_from and Path(identity_from).exists():
        t1 = json.loads(Path(identity_from).read_text())
        if t1.get("identity"):
            rep["identity"] = {**t1["identity"], "carried_from": str(identity_from),
                               "note": "tensor identity is data-independent; same pinned SAE artifact and hook as T1"}
    try:
        from gates._common import GATE_RULES_VERSION
        rep["rules"] = GATE_RULES_VERSION
    except Exception:
        pass
    Path(out_path).write_text(json.dumps(rep, indent=2))
    return rep


def build_known_answer(sae, tok, samples, out_path, feature, extra_features=()):
    """G3 on real spans. Concept positions = tokens inside a ```tool block whose JSON carries code (def/import/
    return/class/print/assert); other positions = reasoning prose outside the block. Statistic: window-max AUROC.
    planted = mean window-max over code windows; baseline = prose windows; mention = prose windows that talk about
    code; control = prose windows that do not."""
    from .sae import encode_dense
    from gates.g3_feature_known_answer import auroc
    feats = [feature] + list(extra_features)
    acts_code, acts_prose, acts_mention, acts_control = {f: [] for f in feats}, {f: [] for f in feats}, {f: [] for f in feats}, {f: [] for f in feats}
    n_code_spans = 0
    for smp in samples:
        regions, text, block = token_regions(tok, smp["span_ids"])
        dense = encode_dense(sae, smp["resid"])
        is_code_block = any(mk in block for mk in CODE_MARKS)
        for f in feats:
            col = dense[:, f]
            blk = [col[i] for i, r in enumerate(regions) if r == "block"]
            pro = [col[i] for i, r in enumerate(regions) if r == "prose"]
            if is_code_block and blk:
                acts_code[f] += _window_max(blk)
            if pro:
                wm = _window_max(pro)
                prose_text = "".join(tok.decode([smp["span_ids"][i]]) for i, r in enumerate(regions) if r == "prose").lower()
                (acts_mention if any(w in prose_text for w in CODE_TALK) else acts_control)[f] += wm
                acts_prose[f] += wm
        n_code_spans += is_code_block
    per = {}
    for f in feats:
        c, p = acts_code[f], acts_prose[f]
        per[str(f)] = {"auroc_window_max": auroc(c, p) if c and p else None, "n_code_windows": len(c), "n_prose_windows": len(p),
                       "mean_code": float(np.mean(c)) if c else None, "mean_prose": float(np.mean(p)) if p else None}
    mf = feature
    rep = {"feature": mf, "statistic": f"window-max ({WINDOW} tokens) AUROC on REAL T3 spans: tool-block code positions vs reasoning prose",
           "concept_positions": [float(x) for x in acts_code[mf]], "other_positions": [float(x) for x in acts_prose[mf]],
           "planted_secrecy_activation": float(np.mean(acts_code[mf])) if acts_code[mf] else 0.0,
           "baseline_secrecy_activation": float(np.mean(acts_prose[mf])) if acts_prose[mf] else 0.0,
           "mention_behavior_activation": float(np.mean(acts_mention[mf])) if acts_mention[mf] else 0.0,
           "control_behavior_activation": float(np.mean(acts_control[mf])) if acts_control[mf] else 0.0,
           "auroc": per[str(mf)]["auroc_window_max"], "frac_active": {"code": float(np.mean([x > 0 for x in acts_code[mf]])) if acts_code[mf] else None,
                                                                    "prose": float(np.mean([x > 0 for x in acts_prose[mf]])) if acts_prose[mf] else None},
           "per_feature": per, "n_spans": len(samples), "n_code_spans": n_code_spans,
           "note": "planted/mention/control fields are real-span analogues: code windows / prose windows mentioning code / prose windows not mentioning code"}
    try:
        from gates._common import GATE_RULES_VERSION
        rep["rules"] = GATE_RULES_VERSION
    except Exception:
        pass
    Path(out_path).write_text(json.dumps(rep, indent=2))
    return rep


def build_oracle_calibration(lm, tok, samples, out_path, backend="karvonen", n_per=6):
    """G5 paired-only on real-span residuals (captured unadapted, before the LoRA loads). 'code' = positions inside
    code-bearing tool blocks; 'prose' = reasoning positions. Residual segments are concatenated per label."""
    from .oracle import load_oracle, oracle_calibration_report
    rng = np.random.default_rng(0)
    segs = {"code": [], "prose": []}; pos = {"code": [], "prose": []}
    for smp in samples:
        regions, text, block = token_regions(tok, smp["span_ids"])
        is_code = any(mk in block for mk in CODE_MARKS)
        for label, want in (("code", "block"), ("prose", "prose")):
            if label == "code" and not is_code:
                continue
            idx = [i for i, r in enumerate(regions) if r == want and i >= 8]
            if len(idx) < 2:
                continue
            offset = sum(len(s) for s in segs[label])
            segs[label].append(np.asarray(smp["resid"], dtype=np.float32))
            pos[label] += [offset + i for i in idx]
    samples5 = {}
    for label in ("code", "prose"):
        if not segs[label]:
            continue
        resid = np.concatenate(segs[label], 0)
        chosen = sorted(rng.choice(pos[label], size=min(n_per, len(pos[label])), replace=False).tolist())
        samples5[label] = (resid, chosen)
    if len(samples5) < 2:
        Path(out_path).write_text(json.dumps({"error": "not enough real-span samples for both labels"}))
        return None
    oracle = load_oracle(backend, lm=lm)
    return oracle_calibration_report(oracle, samples5, out_path)
