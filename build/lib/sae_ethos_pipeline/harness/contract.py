"""Scenario/tool contract integrity. Refuse to run unless, for every variant:

    declared tools (scenario.yaml + variant add_tool)
      == documented tools (protocol.TOOL_DOCS)
      == executor tools   (tools.REGISTRY)

Any mismatch is fatal — a tool the model is told about but can't call, or can call but isn't told
about, or that the harness can't execute, silently changes the experiment. This is the permanent gate
the reviewer asked for."""
from .tools import REGISTRY
from .protocol import TOOL_DOCS


class ContractError(RuntimeError):
    pass


def effective_tools(scenario, variant_spec):
    tools = list(scenario["tools"])
    add = (variant_spec or {}).get("add_tool")
    if add and add not in tools:
        tools.append(add)
    return tools


def check_scenario(scenario):
    """Validate every variant of one scenario. Returns the per-variant tool sets; raises on mismatch."""
    out = {}
    for variant in ("full", "control", "mention"):
        vspec = (scenario.get("variants") or {}).get(variant, {})
        tools = set(effective_tools(scenario, vspec))
        undocumented = tools - set(TOOL_DOCS)
        unexecutable = tools - set(REGISTRY)
        if undocumented or unexecutable:
            raise ContractError(
                f"{scenario['id']}/{variant}: undocumented={sorted(undocumented)} "
                f"unexecutable={sorted(unexecutable)} (declared==documented==executor required)")
        out[variant] = sorted(tools)
    return out
