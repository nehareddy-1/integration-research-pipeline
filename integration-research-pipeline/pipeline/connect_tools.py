"""One-time setup: connect your Tavily and Firecrawl keys to Composio (instead of clicking through the dashboard).

  1. Put TAVILY_API_KEY and FIRECRAWL_API_KEY in .env
  2. python -m pipeline.connect_tools

It creates an API-key auth config for each toolkit (or reuses one), connects your key under
COMPOSIO_USER_ID, then runs one test search and one test scrape.
"""
from __future__ import annotations

import os

from composio import Composio
from composio.core.models.connected_accounts import auth_scheme

from . import config, tools

TOOLKITS = {"tavily": "TAVILY_API_KEY", "firecrawl": "FIRECRAWL_API_KEY"}


# Non-secret fields some toolkits require (e.g. Firecrawl's base URL, a field called "full").
KNOWN_VALUES = {"firecrawl": {"full": "https://api.firecrawl.dev", "base_url": "https://api.firecrawl.dev"}}


def _connection_values(c: Composio, toolkit: str, key: str) -> dict:
    """Ask Composio which fields the connection needs: the key goes in key-like fields,
    other fields get their default (or a known value)."""
    fields = []
    for mode in ("API_KEY", "api_key"):
        try:
            fields = c.toolkits.get_connected_account_initiation_fields(toolkit, mode, required_only=True)
            if fields:
                break
        except Exception:
            pass
    if not fields:
        return {"generic_api_key": key}
    values = {}
    for f in fields:
        if any(w in f.name.lower() for w in ("key", "token", "secret")):
            values[f.name] = key
        elif getattr(f, "default", None):
            values[f.name] = f.default
        elif f.name in KNOWN_VALUES.get(toolkit, {}):
            values[f.name] = KNOWN_VALUES[toolkit][f.name]
        else:
            raise SystemExit(f"   {toolkit} needs a value for field '{f.name}' ({f.description}). Paste this output to fix.")
    return values


def _auth_config_id(c: Composio, toolkit: str) -> str:
    try:
        existing = c.auth_configs.list(toolkit_slug=toolkit)
        for item in existing.items:
            if item.status == "ENABLED":
                print(f"   reusing auth config {item.id}")
                return item.id
    except Exception:
        pass
    created = c.auth_configs.create(toolkit, {"type": "use_custom_auth", "auth_scheme": "API_KEY"})
    print(f"   created auth config {created.id}")
    return created.id


def main():
    import sys
    only = [a.lower() for a in sys.argv[1:]]  # e.g. python -m pipeline.connect_tools firecrawl
    if not config.COMPOSIO_API_KEY:
        raise SystemExit("COMPOSIO_API_KEY missing in .env")
    c = Composio(api_key=config.COMPOSIO_API_KEY)

    for toolkit, env_name in TOOLKITS.items():
        if only and toolkit not in only:
            continue
        key = os.getenv(env_name, "").strip()
        print(f"\n[{toolkit}]")
        if not key:
            print(f"   {env_name} is empty in .env - skipping")
            continue
        ac_id = _auth_config_id(c, toolkit)
        values = _connection_values(c, toolkit, key)
        print("   fields: " + ", ".join(f"{k}={'(your key)' if v == key else v}" for k, v in values.items()))
        req = c.connected_accounts.initiate(
            config.COMPOSIO_USER_ID, ac_id,
            config=auth_scheme.api_key(values),
            allow_multiple=True,
        )
        print(f"   connected account {getattr(req, 'id', '?')} for user '{config.COMPOSIO_USER_ID}' "
              f"(status: {getattr(req, 'status', '?')})")

    print("\n[test] search ...")
    hits = tools.search("Notion API authentication", 2)
    for h in hits:
        print("   ", h["url"])
    print("[test] scrape ...")
    page = tools.scrape("https://developers.notion.com/docs/authorization")
    print(f"    ok={page['ok']} via={page['via']} chars={len(page['text'])}")
    print("\nAll set." if hits and page["ok"] else "\nSomething is still off - paste this output.")


if __name__ == "__main__":
    main()
