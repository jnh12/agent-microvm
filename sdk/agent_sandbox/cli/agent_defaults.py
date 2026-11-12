"""Local, service-scoped preferences for hosted CLI creation."""

from agent_sandbox.accounts import AccountError, read_config, service_url


def saved_default():
    return read_config().get("agent_defaults", {}).get(service_url())


def resolve_agent(explicit, request):
    if explicit is not None:
        if explicit not in {"codex", "claude", "none"}:
            raise AccountError("INVALID_AGENT", "--agent must be codex, claude, or none.")
        return None if explicit == "none" else explicit
    saved = saved_default()
    if saved:
        return saved
    items = request("GET", "/v1/agent-connections").get("items", [])
    providers = {item["provider"] for item in items if item["provider"] in {"codex", "claude"}}
    return next(iter(providers)) if len(providers) == 1 else None
