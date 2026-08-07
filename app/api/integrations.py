"""Public callback route for OAuth-based tool integrations.

Oda's MCP server (and any future OAuth provider) redirects the household
member's browser here after they approve/deny consent. This has to live on
the public app (port 8080), not the admin dashboard (port 9090, LAN-only) —
see docs/oda-grocery-mcp-tool-design.md "Why the callback can't live on the
admin port".

No admin-auth dependency here by necessity: the OAuth provider is the one
calling this URL, not an authenticated admin browser. Security instead
rests entirely on the `state` value being unguessable, single-use, and
short-lived (app.integrations.oauth_state.consume_state) — the same model
any public OAuth redirect_uri uses.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter()


def _result_page(message: str, *, ok: bool) -> HTMLResponse:
    color = "#2ea043" if ok else "#da3633"
    icon = "&#10003;" if ok else "&#10007;"
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>HomeAgent — Integration</title>
<style>
body {{ font-family: system-ui, sans-serif; background:#0d1117; color:#e6edf3;
        display:flex; align-items:center; justify-content:center; height:100vh; margin:0; }}
.card {{ text-align:center; padding:32px 40px; border-radius:8px; background:#161b22;
         border:1px solid #30363d; }}
.icon {{ font-size:40px; color:{color}; margin-bottom:12px; }}
</style></head>
<body><div class="card"><div class="icon">{icon}</div><div>{message}</div></div></body></html>"""
    return HTMLResponse(content=html, status_code=200 if ok else 400)


@router.get("/integrations/{provider}/callback")
async def integration_callback(
    provider: str, code: str = "", state: str = "", error: str = ""
) -> HTMLResponse:
    if provider != "oda":
        return _result_page("Unknown integration provider.", ok=False)

    from app.integrations.oauth_state import consume_state

    # Consume the state unconditionally (if present) so a denied/errored
    # consent doesn't leave the row dangling until its TTL expires.
    pending = consume_state(state) if state else None

    if error:
        logger.warning("Oda OAuth callback returned error=%s", error)
        return _result_page("Connection was cancelled or denied.", ok=False)

    if not code or pending is None:
        return _result_page(
            "This connection link is invalid, expired, or was already used. "
            "Please try connecting again from the admin dashboard.",
            ok=False,
        )

    if pending.provider != provider:
        logger.warning(
            "Oda OAuth callback provider mismatch: state.provider=%s url.provider=%s",
            pending.provider,
            provider,
        )
        return _result_page("Connection request mismatch. Please try connecting again.", ok=False)

    from app.oda import oauth as oda_oauth

    try:
        metadata = await oda_oauth.discover_metadata()
        token = await oda_oauth.exchange_code(
            metadata,
            client_id=pending.client_id,
            client_secret=pending.client_secret,
            code=code,
            redirect_uri=pending.redirect_uri,
            code_verifier=pending.pkce_verifier,
        )
    except oda_oauth.OdaOAuthError:
        logger.warning("Oda token exchange failed", exc_info=True)
        return _result_page("Could not complete the connection to Oda. Please try again.", ok=False)

    from app.integrations.accounts import upsert_account

    upsert_account(
        household_id=pending.household_id,
        provider=provider,
        connected_by_user_id=pending.initiating_user_id,
        client_id=pending.client_id,
        client_secret=pending.client_secret,
        access_token=token.access_token,
        refresh_token=token.refresh_token,
        expires_at=token.expires_at,
    )

    from app.control.events import emit

    emit("integration.connected", {"provider": provider, "household_id": pending.household_id})

    # TODO(Phase 4): once app/oda/mcp_client.py exists, call its start_mcp()
    # (or restart if already running) then app.agent.agent.reload_agent()
    # here so the running agent picks up the new Oda toolset without a
    # process restart — see docs/oda-grocery-mcp-tool-design.md "Flow".

    return _result_page("Oda connected — you can close this tab.", ok=True)
