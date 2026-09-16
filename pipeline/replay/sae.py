#!/usr/bin/env python3
"""Attach the Gemma Scope SAE via SAELens and encode residuals to sparse features.

The SAE is the store of record for features. Neuronpedia labels are pulled once into
features/feature_labels.json.
"""
from pathlib import Path
import json
import numpy as np
import yaml

CFG = Path(__file__).resolve().parent.parent / "config"


def sae_cfg():
    return yaml.safe_load((CFG / "models.yaml").read_text())["sae"]


def _hook_name(sae):
    cfg = sae.cfg
    meta = getattr(cfg, "metadata", None)
    return getattr(meta, "hook_name", None) or getattr(cfg, "hook_name", None)


def load_sae(device="cuda"):
    """SAE.from_pretrained on the EXACT canonical artifact from models.yaml (release + sae_id).
    Asserts the loaded cfg's hook_name equals models.yaml sae.saelens_hook_name — that string is the
    contract between the SAE and the residual capture."""
    from sae_lens import SAE
    s = sae_cfg()
    out = SAE.from_pretrained(s["release"], s["sae_id"], device=device)
    sae = out[0] if isinstance(out, tuple) else out
    hn = _hook_name(sae)
    if hn != s["saelens_hook_name"]:
        raise RuntimeError(f"SAE hook_name {hn!r} != models.yaml saelens_hook_name {s['saelens_hook_name']!r}")
    return sae


def _to_sae(sae, residual):
    import torch
    x = torch.as_tensor(np.asarray(residual, dtype=np.float32))
    return x.to(sae.device, sae.dtype)


def encode_dense(sae, residual):
    """[seq, d_model] -> [seq, n_features] float32 numpy (post-JumpReLU activations)."""
    import torch
    with torch.no_grad():
        return sae.encode(_to_sae(sae, residual)).float().cpu().numpy()


def encode(sae, residual: "np.ndarray"):
    """residual [seq, d_model] -> list of (pos, feature, act) for nonzero activations."""
    acts = encode_dense(sae, residual)
    pos, feat = np.nonzero(acts > 0)
    return [(int(p), int(f), float(acts[p, f])) for p, f in zip(pos, feat)]


def sae_health(sae, residual: "np.ndarray", skip_bos=True):
    """Variance-explained and mean L0 for residuals at ONE hook.

    L0 = number of active features per token, averaged over tokens (NOT density).
    FVU = sum||x - x_hat||^2 / sum||x - mean(x)||^2 over tokens (the fraction-of-variance-unexplained
    definition used by the Gemma Scope report); var_explained = 1 - FVU. Also reports the per-token
    SAELens-style explained variance for reference. BOS is excluded by default (its norm is an outlier
    that dominates any variance statistic)."""
    import torch
    x = _to_sae(sae, residual)
    if skip_bos and x.shape[0] > 1:
        x = x[1:]
    with torch.no_grad():
        acts = sae.encode(x)
        x_hat = sae.decode(acts)
        xf, xh = x.float(), x_hat.float()
        l0 = (acts > 0).sum(dim=-1).float().mean().item()
        resid_ss = ((xf - xh) ** 2).sum().item()
        total_ss = ((xf - xf.mean(dim=0, keepdim=True)) ** 2).sum().item()
        fvu = resid_ss / max(total_ss, 1e-9)
        # per-token variant (SAELens explained_variance): 1 - mse_tok / var_tok, averaged
        mse_tok = ((xf - xh) ** 2).mean(dim=-1)
        var_tok = xf.var(dim=-1)
        ev_tok = (1 - mse_tok / var_tok.clamp_min(1e-9)).mean().item()
        mse_norm = (((xf - xh) ** 2).sum(-1) / (xf ** 2).sum(-1)).mean().item()
    return {"var_explained": 1.0 - fvu, "fvu": fvu, "l0": l0, "var_explained_per_token": ev_tok,
            "rel_mse": mse_norm, "n_tokens": int(x.shape[0])}


def hook_identification_report(lm, sae, residuals_by_hook, out_path):
    """Health at the CHOSEN hook AND every decoy; emits the JSON G2 consumes.
    residuals_by_hook: {hook_name: np.ndarray[seq, d_model]}. Writes {chosen, candidates, published}."""
    s = sae_cfg()
    chosen_name = s["hook_point"]
    chosen = {"hook": chosen_name, **sae_health(sae, residuals_by_hook[chosen_name])}
    cands = [{"hook": h, **sae_health(sae, residuals_by_hook[h])}
             for h in s.get("hook_candidates", []) if h in residuals_by_hook]
    rep = {"chosen": chosen, "candidates": cands, "published": dict(s["published"]),
           "sae": {"release": s["release"], "sae_id": s["sae_id"]}}
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(rep, indent=2))
    return rep


def fetch_neuronpedia_labels(feature_indices, out_path, model_id="gemma-2-9b-it",
                             source="31-gemmascope-res-16k"):
    """Pull labels for the given features into feature_labels.json (network step, not GPU)."""
    import urllib.request
    out = {}
    for idx in feature_indices:
        url = f"https://www.neuronpedia.org/api/feature/{model_id}/{source}/{idx}"
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                d = json.loads(r.read().decode())
            exps = d.get("explanations") or []
            out[str(idx)] = {"label": exps[0]["description"] if exps else None,
                             "explanations": [e.get("description") for e in exps], "url": url}
        except Exception as e:                     # keep going; label absence is not a gate failure
            out[str(idx)] = {"label": None, "error": str(e)[:120], "url": url}
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(out, indent=2))
    return out
