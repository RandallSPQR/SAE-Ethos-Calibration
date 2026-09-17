#!/usr/bin/env python3
"""OpenAI-compatible client for the vLLM target endpoint. Real code — works against any such endpoint;
no GPU needed on the caller side. Falls back to a deterministic mock when --mock or no endpoint.
"""
import os
from pathlib import Path
import yaml

CFG = Path(__file__).resolve().parent.parent / "config"


class TargetClient:
    def __init__(self, mock=False):
        models = yaml.safe_load((CFG / "models.yaml").read_text())
        self.ep = models["endpoint"]
        self.model = self.ep["served_model_name"]
        self.mock = mock
        self._client = None

    def _lazy(self):
        if self._client is None and not self.mock:
            from openai import OpenAI       # deferred; only needed for real calls
            key = os.environ.get(self.ep["api_key_env"], "x")
            uds = os.environ.get("TARGET_UDS") or self.ep.get("uds")
            if uds:
                # vLLM served on a unix domain socket (`vllm serve --uds`): reachable from inside a bwrap
                # worker whose network namespace is OFF (harness.isolation strong path). The URL host is a
                # placeholder; httpx routes every request through the socket.
                import httpx
                self._client = OpenAI(base_url="http://vllm-uds/v1", api_key=key,
                                      http_client=httpx.Client(transport=httpx.HTTPTransport(uds=uds), timeout=600.0))
            else:
                self._client = OpenAI(base_url=self.ep["base_url"], api_key=key)
        return self._client

    def complete(self, messages, temperature, top_p, max_tokens, seed=None):
        """Return {'text', 'token_ids', 'token_logprobs'}. token_ids are the RAW ids the target emitted;
        get them from vLLM's `return_token_ids` (via extra_body) rather than a fragile OpenAI-style
        logprob `token_id` attribute. Missing raw ids is a hard G1 failure upstream, never a retokenize.

        The prompt is built by the ONE canonical Gemma-2 serializer (no system role, alternating turns,
        tool results as user turns) and sent via the completions endpoint, so generation and replay are
        byte-identical. We do NOT send OpenAI chat roles to a Gemma template that would reject them."""
        if self.mock:
            return self._mock(messages, seed)
        from model_io.gemma2 import serialize_messages
        prompt = serialize_messages(messages, add_generation_prompt=True)
        r = self._lazy().completions.create(
            model=self.model, prompt=prompt,
            temperature=temperature, top_p=top_p, max_tokens=max_tokens, seed=seed,
            logprobs=2,        # top-2 so generation records its own margin at every position (G1 flip excuse)
            # add_special_tokens EXPLICIT (do not inherit the vLLM default); return prompt + gen token ids
            # so G0a can compare prompt identity, not just completions.
            extra_body={"return_token_ids": True, "add_special_tokens": True})
        choice = r.choices[0]
        # completions endpoint shapes: choice.text, choice.logprobs.token_logprobs, vLLM token_ids
        ids = getattr(choice, "token_ids", None)       # vLLM return_token_ids
        lp = getattr(choice, "logprobs", None)
        lps = list(lp.token_logprobs) if lp and getattr(lp, "token_logprobs", None) else None
        if ids is None and lp is not None:
            ids = getattr(lp, "token_ids", None)
        margins = None
        tops = getattr(lp, "top_logprobs", None) if lp is not None else None
        if tops:
            margins = []
            for d in tops:
                vals = sorted((d or {}).values(), reverse=True)
                margins.append(float(vals[0] - vals[1]) if len(vals) >= 2 else None)
        return {"text": choice.text, "token_ids": ids, "token_logprobs": lps, "top2_margin": margins}

    @staticmethod
    def _mock(messages, seed):
        last = next((m["content"] for m in reversed(messages) if m["role"] == "tool"), "")
        if (seed or 0) % 2 == 0:
            text = f"I'll use what I found ({last[:20]}) and submit."
        else:
            text = "The README forbids that resource; I'll take the honest path and ask."
        ids = [hash((text, i)) % 32000 for i in range(len(text.split()))]
        return {"text": text, "token_ids": ids, "token_logprobs": [-0.3] * len(ids), "top2_margin": [1.0] * len(ids)}


def _to_openai(messages):
    out = []
    for m in messages:
        if m["role"] == "tool":
            out.append({"role": "tool", "content": m["content"], "name": m.get("name", "tool"),
                        "tool_call_id": m.get("name", "tool")})
        else:
            out.append({"role": m["role"], "content": m["content"]})
    return out
