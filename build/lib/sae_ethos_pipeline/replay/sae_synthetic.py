"""SMOKE ONLY: a random JumpReLU-shaped SAE with the attributes replay.sae.encode uses (encode, decode, device, dtype,
threshold), so the span-scoring path can run end to end on a model without its SAE downloaded. Its features mean nothing;
nothing scored with it is analysed (span_score records synthetic_sae in SPAN_SCORE.json)."""
import torch


class SyntheticSAE:
    def __init__(self, d_in, d_sae, seed=0):
        g = torch.Generator().manual_seed(seed)
        self.W_enc = torch.randn(d_in, d_sae, generator=g) / d_in ** 0.5
        self.W_dec = self.W_enc.T.clone()
        self.threshold = torch.full((d_sae,), 0.5)
        self.device, self.dtype = torch.device("cpu"), torch.float32

    def encode(self, x):
        pre = x.float() @ self.W_enc
        return pre * (pre > self.threshold)

    def decode(self, a):
        return a @ self.W_dec
