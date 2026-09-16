"""The real agent loop: target model <-> real tools over a Sandbox. Deterministic given model outputs.

Decision-point resampling requires RESTORING conversation state, not starting fresh. build_prefix runs
the deterministic (T=0) prefix up to a STRUCTURED obstacle trigger (harness.triggers), stopping before
the decision turn, and HARD-FAILS if the reveal never fires — so we can never snapshot a state where
the obstacle wasn't reached. continue_from resumes from the restored prefix + restored filesystem; its
first assistant turn IS the decision point.

ask_user is NOT terminal while an escalation turn is pending — otherwise the "But you did it before!"
pressure test could never be delivered after the model takes the benign path.
"""
from .protocol import system_prompt, parse_tool_call
from .tools import available
from .triggers import TriggerMatcher, DecisionPointNotReached


def _emit(client, messages, temperature, top_p, max_new_tokens, seed):
    resp = client.complete(messages, temperature, top_p, max_new_tokens, seed=seed)
    if isinstance(resp, dict):
        return resp["text"], resp.get("token_ids"), resp.get("token_logprobs")
    return resp, None, None


def build_prefix(client, sandbox, scenario, task_prompt, matcher: TriggerMatcher, max_tool_calls,
                 max_new_tokens, seed=0):
    """Deterministic prefix to the structured obstacle reveal. Returns (prefix_messages, start_step).
    Raises DecisionPointNotReached if the trigger never fires (mis-designed scenario or degenerate run)."""
    tools = available(scenario["tools"])
    messages = [{"role": "system", "content": system_prompt(scenario["tools"])},
                {"role": "user", "content": task_prompt}]
    for step in range(max_tool_calls + 2):
        text, ids, lps = _emit(client, messages, 0.0, 1.0, max_new_tokens, seed + step)
        messages.append({"role": "assistant", "content": text, "tool_calls": [],
                         "tokens": {"sampled_ids": ids, "sampled_logprobs": lps}})
        call = parse_tool_call(text)
        if call is None:
            raise DecisionPointNotReached(
                f"{scenario['id']}: model produced no tool call before the obstacle was revealed "
                f"(step {step}). Prefix cannot be established; not a valid decision-point run.")
        name, args = call
        messages[-1]["tool_calls"] = [{"name": name, "args": args}]
        if matcher.on_tool_call(name, args):
            return messages, step + 1
        if name not in tools:
            messages.append({"role": "tool", "name": name, "content": f"error: no such tool '{name}'"})
            continue
        result, meta = tools[name](sandbox, **args) if args else tools[name](sandbox)
        messages.append({"role": "tool", "name": name, "content": result})
        if matcher.on_tool_result(name, args, result):
            return messages, step + 1          # stop BEFORE the decision assistant turn
        if meta.get("terminal"):
            raise DecisionPointNotReached(
                f"{scenario['id']}: episode terminated ({meta.get('terminal')}) before the obstacle was "
                f"revealed. The deterministic path does not reach the obstacle.")
    raise DecisionPointNotReached(f"{scenario['id']}: tool-call cap hit before obstacle reveal.")


def continue_from(client, sandbox, scenario, prefix_messages, start_step,
                  max_tool_calls, temperature, top_p, max_new_tokens, seed=0, escalation_turns=None):
    """Resume from a RESTORED prefix (and restored filesystem). First assistant turn = decision point."""
    tools = available(scenario["tools"])
    messages = [dict(m) for m in prefix_messages]
    decision_point = None
    decision_tokens = None
    escalated = False
    esc = list(escalation_turns or [])

    for step in range(start_step, max_tool_calls + 2):
        text, ids, lps = _emit(client, messages, temperature, top_p, max_new_tokens, seed + step)
        msg = {"role": "assistant", "content": text, "tool_calls": [],
               "tokens": {"sampled_ids": ids, "sampled_logprobs": lps}}
        messages.append(msg)
        if decision_point is None:
            decision_point = len(messages) - 1
            decision_tokens = {"sampled_ids": ids, "sampled_logprobs": lps, "text": text}

        call = parse_tool_call(text)
        if call is None:
            if esc and not escalated:
                messages.append({"role": "user", "content": esc.pop(0)})
                escalated = True
                continue
            return messages, decision_point, {"terminal": "no_tool"}, decision_tokens
        name, args = call
        msg["tool_calls"] = [{"name": name, "args": args}]
        if name not in tools:
            messages.append({"role": "tool", "name": name, "content": f"error: no such tool '{name}'"})
            continue
        result, meta = tools[name](sandbox, **args) if args else tools[name](sandbox)
        messages.append({"role": "tool", "name": name, "content": result})
        # ask_user is terminal ONLY when no escalation remains — otherwise deliver the pressure turn
        if name == "ask_user" and esc and not escalated:
            messages.append({"role": "user", "content": esc.pop(0)})
            escalated = True
            continue
        if meta.get("terminal"):
            return messages, decision_point, meta, decision_tokens
    return messages, decision_point, {"terminal": "cap"}, decision_tokens
