"""Offline check (python -m model_io.test_gemma3): the gemma3 serializer reproduces Gemma-3-27B-IT's own chat template
(verbatim, model_io/fixtures/) on conversations the template accepts, minus the BOS the template emits itself (our
string carries none; the tokenizer adds exactly one). Conversations the template rejects (non-alternating turns, tool
roles) are the serializer's folding, which the template has no answer for; those are checked for structure only."""
import sys
from pathlib import Path

import jinja2

from model_io import gemma3

TPL = (Path(__file__).parent / "fixtures" / "gemma3_chat_template.jinja").read_text()


def render(msgs, gen=True):
    def rx(m):
        raise ValueError(m)
    env = jinja2.Environment()
    env.globals["raise_exception"] = rx
    return env.from_string(TPL).render(messages=msgs, add_generation_prompt=gen, bos_token="<bos>")


CASES = {
    "plain": [{"role": "user", "content": "Say hi."}],
    "system": [{"role": "system", "content": "You are careful."}, {"role": "user", "content": "Fix the bug."},
               {"role": "assistant", "content": "Done."}, {"role": "user", "content": "Again."}],
    "whitespace": [{"role": "user", "content": "  padded \n"}, {"role": "assistant", "content": "\nreply with newline\n\n"},
                   {"role": "user", "content": "next"}],
    "system_trailing_space": [{"role": "system", "content": "Sys text  "}, {"role": "user", "content": " u "}],
    "multiline_code": [{"role": "user", "content": "```python\ndef f():\n    return 1\n```"},
                       {"role": "assistant", "content": "```tool\n{\"name\": \"run_tests\", \"args\": {}}\n```"},
                       {"role": "user", "content": "Tool result from run_tests:\n2 passed"}],
}


def main():
    res = {}
    for name, msgs in CASES.items():
        for gen in (True, False):
            want = render(msgs, gen)
            assert want.startswith("<bos>")
            got = gemma3.serialize_messages(msgs, add_generation_prompt=gen)
            res[f"{name}/gen={gen}"] = got == want[len("<bos>"):]
            if not res[f"{name}/gen={gen}"]:
                print("MISMATCH", name, gen, repr(got[:200]), repr(want[5:205]))
    # folding the template cannot express: a tool turn after a user turn coalesces into one user turn
    fold = gemma3.serialize_messages([{"role": "user", "content": "go"}, {"role": "tool", "name": "git", "content": " ok \n"}])
    res["tool_after_user_folds"] = fold == "<start_of_turn>user\ngo\n\nTool result from git:\nok<end_of_turn>\n<start_of_turn>model\n"
    # the 9B serializer is untouched (no trim): its whitespace case differs from gemma3's
    from model_io import gemma2
    res["gemma2_unchanged_no_trim"] = "\nreply with newline\n\n" in gemma2.serialize_messages(CASES["whitespace"])
    for k, v in res.items():
        print(("PASS " if v else "FAIL ") + k)
    bad = [k for k, v in res.items() if not v]
    print(f"\n{len(res) - len(bad)}/{len(res)} passed")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
