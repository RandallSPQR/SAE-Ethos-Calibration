"""Structured obstacle-reveal predicates. The prose `revealed_by` field is documentation; THIS is the
executable logic. A trigger matches a specific tool call/result (optionally by args/result substrings
and occurrence), so the decision point is the assistant turn after a PRECISE event — not 'any read_file'.

Trigger schema (scenario.yaml obstacle.trigger), surface-substituted before use:
  type: tool_result | tool_call     # match when a tool RESULT arrives, or when a tool is CALLED
  tool: run_tests                   # required: the tool name
  occurrence: 1                     # optional: the nth match (default 1)
  args_contains: "commit -S"        # optional substring in the JSON-encoded args
  result_contains: "gpg"            # optional substring in the tool result text
"""
import json


class DecisionPointNotReached(RuntimeError):
    """Raised by build_prefix when the deterministic path does not reach the obstacle. Carries the prefix
    transcript so far (`messages`) so an excluded cell can be retuned offline instead of vanishing."""
    def __init__(self, msg, messages=None):
        super().__init__(msg)
        self.messages = messages


class TriggerMatcher:
    def __init__(self, trigger):
        self.t = trigger
        self.tool = trigger["tool"]
        self.type = trigger.get("type", "tool_result")
        self.occurrence = int(trigger.get("occurrence", 1))
        self.args_contains = trigger.get("args_contains")
        self.result_contains = trigger.get("result_contains")
        self._hits = 0

    def on_tool_call(self, name, args):
        if self.type != "tool_call":
            return False
        return self._match(name, args, None)

    def on_tool_result(self, name, args, result):
        if self.type != "tool_result":
            return False
        return self._match(name, args, result)

    def _match(self, name, args, result):
        tools = self.tool if isinstance(self.tool, (list, tuple)) else [self.tool]
        if name not in tools:
            return False
        if self.args_contains and self.args_contains not in json.dumps(args or {}):
            return False
        if self.result_contains and (result is None or self.result_contains not in str(result)):
            return False
        self._hits += 1
        return self._hits >= self.occurrence


def build_matcher(scenario, surface):
    trig = scenario.get("obstacle", {}).get("trigger")
    if not trig:
        raise ValueError(f"{scenario.get('id')}: obstacle.trigger is required (prose revealed_by is not "
                         f"executable). Add a structured trigger predicate.")
    trig = _subst(dict(trig), surface)
    return TriggerMatcher(trig)


def _subst(trig, surface):
    for k in ("tool", "args_contains", "result_contains"):
        if isinstance(trig.get(k), str):
            for sk, sv in surface.items():
                trig[k] = trig[k].replace("{{" + sk + "}}", str(sv))
    return trig
