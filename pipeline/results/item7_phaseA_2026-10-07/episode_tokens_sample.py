import json, glob, sys, collections, random
sys.path.insert(0, ".")
import modelcfg
from transformers import AutoTokenizer
tok = AutoTokenizer.from_pretrained("/Users/randallbennington/.cache/huggingface/hub/models--google--gemma-3-4b-it/snapshots/093f9f388b31de276ce2de164bdc2081324b9767")
ser = modelcfg.serializer().apply_to_tokenizer
rows = [json.loads(l) for f in sorted(glob.glob("results/t4_27b_2026-09-30_t3/relabel_2026-10-02.1/generation/arm_a/*.jsonl")) for l in open(f) if l.strip()]
by = collections.defaultdict(list)
for r in rows: by[(r["scenario"], r["variant"])].append(r)
random.seed(7); out = {}
for k, rs in sorted(by.items()):
    sm = random.sample(rs, min(25, len(rs)))
    turns, final, cum_in, outt = [], [], [], []
    for r in sm:
        msgs = r["messages"]; ai = [i for i, m in enumerate(msgs) if m["role"] == "assistant"]
        turns.append(len(ai))
        final.append(len(ser(tok, msgs, add_generation_prompt=False)))
        ci = 0; ot = 0
        for i in ai:
            ci += len(ser(tok, msgs[:i], add_generation_prompt=True))
            ot += len(tok.encode(msgs[i]["content"] or "", add_special_tokens=False))
        cum_in.append(ci); outt.append(ot)
    m = lambda x: sum(x) / len(x)
    out[f"{k[0]}|{k[1]}"] = {"sampled": len(sm), "turns_mean": m(turns), "final_tokens_mean": m(final), "api_input_tokens_mean": m(cum_in), "output_tokens_mean": m(outt),
                            "decision_point_mean": m([r["decision_point"] for r in sm])}
    print(k, {a: round(b, 1) for a, b in out[f"{k[0]}|{k[1]}"].items()}, flush=True)
json.dump(out, open(sys.argv[1], "w"), indent=1)
