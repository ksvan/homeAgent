# Oda Grocery MCP Tool — Design

Status: Phase 1 (OAuth + storage foundation) implemented. Phases 2–5
(admin Integrations page, public callback + lifecycle wiring, MCP client +
policy gate, agent behaviour + instructions.md) not started.
Last code check: 2026-08-06
Implemented runtime entry points: `app/models/integrations.py`
(`IntegrationAccount`), `app/models/cache.py` (`OAuthState`),
`app/integrations/crypto.py` (Fernet key derivation), `app/integrations/accounts.py`
(household-scoped account repository + single-flight refresh),
`app/oda/oauth.py` (OAuth client: discovery, PKCE, DCR, token exchange/refresh),
`alembic/versions/0015_users_db_integration_account.py`,
`alembic/versions/0007_cache_db_oauth_state.py`.
Planned (not yet built): `app/oda/mcp_client.py` (MCP wiring),
`app/control/api.py` (new `/admin/integrations` routes), `app/api/server.py`
(new public `/integrations/{provider}/callback` route),
`app/control/dashboard.html` (new "Integrations" page/tab),
`app/policy/default_policies.py` (Oda tool policies)

## Purpose

Add Oda (Norwegian online grocery delivery) as a tool the agent can use to
search products/recipes, manage a shared household shopping cart, and check
delivery slots and order status — via Oda's own official remote MCP server
at `https://oda.com/mcp`.

The agent prepares; a human finishes. There is no checkout/payment tool in
Oda's MCP surface (confirmed from the tool's own description: *"Selve
betaling og checkout skjer i nettbutikken"*). The agent can build up a cart
and tell the household it's ready — a parent completes delivery-time
selection and payment on oda.com itself. This removes the one thing that
would otherwise make this a high-stakes integration: **the agent cannot
spend money.**

## Confirmed protocol details

Probed directly against `oda.com` (not documented publicly anywhere yet —
this is a newly published endpoint):

- `POST https://oda.com/mcp` → `401` with
  `WWW-Authenticate: Bearer resource_metadata="https://oda.com/.well-known/oauth-protected-resource/mcp"`.
  This is a standard MCP **remote server requiring OAuth**, not a bare URL
  like Homey/Prometheus.
- Protected-resource metadata: `authorization_servers: ["https://oda.com/o"]`,
  `scopes_supported: ["mcp"]`.
- Authorization-server metadata (`https://oda.com/o/.well-known/oauth-authorization-server`):
  `authorization_endpoint`, `token_endpoint`, `revocation_endpoint`, and a
  `registration_endpoint` (dynamic client registration — no pre-issued
  `client_id`). `grant_types_supported: ["authorization_code", "refresh_token"]`,
  `code_challenge_methods_supported: ["S256"]` (PKCE required).

This is the **first user-delegated OAuth integration** in the codebase.
Precedent (`app/wine/graph_client.py`) only does app-level
`client_credentials` — no user consent, no refresh-token lifecycle, nothing
to store per install. Oda needs a real (if small) OAuth client: dynamic
client registration once, then an authorization-code + PKCE flow, then
ongoing refresh-token handling.

## Tool inventory (from Oda's own MCP tool listing)

| Group | Tools | Kind |
|---|---|---|
| Cart & ordering | `get_cart`, `manipulate_cart`, `get_delivery_addresses`, `get_delivery_slots`, `select_delivery_slot` | write (reversible, no charge) except `get_*` |
| Search & discovery | `product_search`, `get_category`, `get_brand`, `similar_and_related_products`, `likely_to_buy`, `unique_for_you` | read |
| Recipes | `recipe_search`, `get_liked_recipes`, `get_purchased_recipes` | read |
| Lists | `get_product_lists`/`get_dinner_lists`, `get_product_list` | read |
| Order history | `get_orders`, `get_order`, `order_tracking` | read |
| Other | `feedback` (about the MCP tools themselves) | write, no real-world effect |

None of these tools place an order or move money. `manipulate_cart` and
`select_delivery_slot` are the only tools with a real side effect, and both
are trivially reversible (edit the cart again, or the household still
finishes checkout manually and can change the slot on oda.com).

## Confirmed via live testing (2026-08-06)

Two of the three original open questions are resolved with real schemas and
a real (redacted) `get_cart` response, captured against a live Oda MCP
connection (`docs/oda.md`, working notes — not committed as a permanent
doc).

**Cart link — resolved.** `get_cart`'s response has a top-level `url` field:
`"https://oda.com/no/cart/"`. This is exactly the deep link the "Groceries
(Oda)" instructions section needs for "here's your cart, finish it on
oda.com" — use the returned field rather than hardcoding the path, in case
it varies later (e.g. by locale).

**`manipulate_cart` schema — resolved:**

```text
manipulate_cart(operations: [{
  quantity: int,                    # required; delta unless overrideQuantity
  overrideQuantity?: bool,          # set exact quantity; product_id only
  productId?: int | null,
  recipeId?: int | null,
  productListId?: int | null,       # shopping list or dinner list
  orderNumber?: string | null,      # re-adds ALL products from that order;
                                     # quantity/negative-quantity is ignored
  fromRecipePortions?: int | null,
}])
# exactly one of productId / recipeId / productListId / orderNumber per operation
```

This confirms the "prefer the previously-bought version" instructions.md
rule is directly actionable: look up the `productId` from `get_orders` /
`get_order` line items rather than guessing from search results.

It also surfaces a blast-radius nuance worth carrying into the
confirmation-message design: an `orderNumber` operation re-adds an entire
past order in one call, not a single item — a `manipulate_cart` confirmation
prompt should summarize what the operation(s) actually contain (item count,
or "re-add order #X") rather than a generic "update the cart?", since one
call can range from "add 1 item" to "add 35 items from a past order."

**`select_delivery_slot` schema — resolved**, matches what the design
assumed: `delivery_slot_id` (required), `delivery_address_id`,
`is_unattended_delivery`. Slots carry `cutoffTime` (order-by deadline for
that slot) and a currently-always-`null` `expireAt` (no evidence of a
selection-expiry window from this one sample) — no URL field on slots.

**`feedback` — rate-limited**: max 500 chars, restricted character set,
**one submission per user per 15 minutes** server-side. `app/oda/mcp_client.py`
should treat a rate-limit error from this tool as an expected outcome to
surface to the agent, not something to retry in a loop.

**Still open**: token/refresh lifetime (`expires_in`, granted scopes) —
not visible through a third-party MCP client, confirmed by directly asking;
this can only be observed from our own token-exchange response once Phase 1
builds it. DCR persistence/re-registration behavior is likewise still
theoretical until we've registered a client for real.

## Household account model

One shared Oda account for the whole household — not per-member. This
matches how `app/homey/mcp_client.py` and `app/prometheus/mcp_client.py`
already assume a single household per deployment (module-level singleton
`MCPToolset`, no household-scoped connection pooling). A parent logs into
their own Oda account once via the admin dashboard; every household member
talking to the agent shares that one cart, same as if they wrote a shared
paper shopping list.

This is explicitly an Oda-specific decision, not a general rule — a future
OAuth tool might legitimately need per-member accounts (e.g. each person's
own calendar). The admin "Integrations" page (below) is designed so
per-member vs. shared-account is a per-provider choice, not baked into the
framework.

## New: admin "Integrations" page

Generalize now, minimally, since this is the first of what will likely be
several OAuth/settings-based tools. Not a plugin framework — just don't
hardcode "Oda" into the table/route names where a `provider` string does the
job just as well.

**New table** `IntegrationAccount` (users.db), scoped to `household_id`
rather than globally-unique `provider` — `household_id` is threaded through
~63 files in this codebase (world model, tasks, policies, users), so even
though the Homey/Prometheus MCP modules currently ignore it, a new table
should not add a second, inconsistent single-tenant assumption:

```text
id: str (pk)
household_id: str (fk Household.id)
provider: str             # "oda"
access_token: str         # encrypted at rest, see below
refresh_token: str        # encrypted at rest
expires_at: datetime
client_id: str            # from dynamic client registration
client_secret: str        # encrypted; empty if DCR returned "none" auth
connected_by_user_id: str (fk User.id)   # who authorized it, for the admin UI
connected_at: datetime
updated_at: datetime

UniqueConstraint(household_id, provider)
```

`_get_household_id()` (`app/control/api.py:594`, grabs the single
`Household` row) is the existing precedent for resolving "the" household in
admin routes — reuse it rather than inventing a second lookup.

**Encryption**: `app_secret_key` is only guaranteed to be an arbitrary
≥32-char string (`app/__main__.py`'s production check), not a valid Fernet
key (Fernet requires exactly 32 url-safe base64-encoded bytes). Derive the
Fernet key instead of using the setting directly:

```python
import base64
import hashlib

def _derive_fernet_key(secret: str) -> bytes:
    digest = hashlib.sha256(f"oda-integration-tokens-v1:{secret}".encode()).digest()
    return base64.urlsafe_b64encode(digest)
```

A fixed purpose string as HKDF-style context keeps this key distinct from
any other future derived key sharing the same `app_secret_key`. Rotating
`app_secret_key` invalidates all stored tokens (decrypt failure) — treat
that as expected: catch the decryption error, drop the row, and surface
"Oda disconnected — please reconnect" in the admin Integrations page rather
than crashing `start_mcp()`.

**New admin routes** (`app/control/api.py`, LAN-only port 9090, existing
`dependencies=_auth`):

- `GET /admin/integrations` — list known providers, connection status
  (connected/not, connected-by, token expiry), read-only.
- `POST /admin/integrations/{provider}/connect` — generates PKCE
  verifier/challenge + `state`, stashes them server-side (short TTL row in
  `cache.db`, same pattern as `PendingAction`), returns the provider's
  `authorize_url` for the admin browser to navigate to. Reconnecting after
  an existing connection replaces the row (re-running `connect` while
  already connected is allowed — treated as a refresh/re-auth, not an
  error).
- `POST /admin/integrations/{provider}/disconnect` — revokes (best-effort,
  `revocation_endpoint`), deletes the `IntegrationAccount` row, stops the
  MCP client, and reloads the agent (see "Connect/disconnect lifecycle"
  below).

**`OAuthState` (`cache.db`)** — the stashed PKCE/state row, one-time and
short-lived (10 min TTL, matching the human-timescale of an OAuth consent
screen):

```text
id: str (pk)                # the "state" value itself
provider: str
household_id: str
initiating_user_id: str      # which admin session started this
pkce_verifier: str
redirect_uri: str            # exact URI used in the authorize request; the
                              # callback must reuse the identical value —
                              # OAuth token exchange requires an exact match
expires_at: datetime
```

The callback handler consumes it atomically: read + delete in the same
transaction, reject if missing/expired (replay or stale link), and verify
the `state` from the query string matches the row's `id` before proceeding.

**New dashboard page**: a simple "Integrations" tab in
`app/control/dashboard.html` listing providers as cards (name, status badge,
Connect/Disconnect button) — same visual language as the existing
tabs (World Model, Tasks, Scheduler, Event Rules).

### Why the callback can't live on the admin port

`docker-compose.yml` only publishes port 8080 through the cloudflared
tunnel; `admin_port` (9090) binds to `127.0.0.1`/LAN by design
(`docs/*` and existing compose comments treat admin as trusted-network-only).
Oda's redirect after consent needs a **publicly reachable** HTTPS URL, so the
OAuth callback route has to live on the main app (`app/api/server.py`,
port 8080) alongside the Telegram webhook, not on the admin app.

Both apps run in the same process (`app/__main__.py` starts `main_server`
and `admin_server` as sibling uvicorn servers sharing in-process state), so
the PKCE `state`/verifier written by the admin-port `connect` call is
readable by the main-port `callback` handler via the shared `cache.db` —
no cross-process coordination needed.

**New setting**: `oda_oauth_public_base_url: str = ""` in `Settings`,
following the existing per-feature pattern (`flight_webhook_public_base_url`,
`agentmail_webhook_public_url` — this codebase does not have one shared
"public base URL" setting; each feature that needs a public callback gets
its own). This is the authoritative source for building
`redirect_uri` — never inferred from the incoming request (which for a
tunneled/proxied deployment may show internal host/port).

**Flow:**

1. Household member on the admin dashboard (LAN) clicks "Connect" on the
   Oda card → `POST /admin/integrations/oda/connect` (admin port). Handler
   builds `redirect_uri = f"{settings.oda_oauth_public_base_url}/integrations/oda/callback"`,
   generates PKCE verifier/challenge + `state`, writes the `OAuthState` row
   (household_id from `_get_household_id()`, initiating admin user,
   `redirect_uri`), and returns the `authorize_url` for the browser to
   navigate to.
2. They log into oda.com with their own Oda credentials (their browser,
   their session — HomeAgent never sees the password) and approve the `mcp`
   scope.
3. Oda redirects to `GET /integrations/oda/callback?code=...&state=...` on
   the **public** app. The handler: looks up and atomically deletes the
   `OAuthState` row by `state`, rejects if missing/expired, exchanges
   `code` and `pkce_verifier` for tokens at `oda.com/o/token/` using the
   **exact** stored `redirect_uri`, encrypts and upserts the `IntegrationAccount` row
   (`household_id` from the state row), then:
   - calls `app.oda.mcp_client.start_mcp()` (or restarts it if already
     running from a prior connection),
   - calls `app.agent.agent.reload_agent()` so the running agent picks up
     the newly-registered Oda toolset without a process restart,
   - shows a plain "Connected — you can close this tab" page.

Disconnect is the mirror image: `stop_mcp()` then `reload_agent()` before
returning from `POST /admin/integrations/oda/disconnect`. At process
startup, `start_mcp()` checks for an existing `IntegrationAccount` row the
same way Homey/Prometheus check for a configured URL — no account, no
toolset, agent built without Oda tools, exactly as today for an
unconfigured Homey/Prometheus deployment.

## MCP client wiring (`app/oda/mcp_client.py`)

Mirrors `app/homey/mcp_client.py`'s shape (module singleton, `start_mcp()` /
`stop_mcp()` with retry/backoff, `get_mcp_toolset()`), with one addition: a
small `httpx.Auth` subclass (`OdaTokenAuth`) implementing
`async_auth_flow()` that attaches `Authorization: Bearer <access_token>` to
each request and, on a `401`, refreshes and retries once.

**Verified against the installed `pydantic-ai==2.21.0`** (not just read from
signatures): `MCPToolset`'s `_build_transport()` treats a URL-shaped client
(`"https://oda.com/mcp"` qualifies) as needing an explicit HTTP transport
whenever `auth` is set, and passes it straight through to FastMCP's
`StreamableHttpTransport(url=..., auth=auth, ...)`. So
`MCPToolset(settings.oda_mcp_url, auth=OdaTokenAuth(...), process_tool_call=...)`
is the right shape — no spike risk here, confirmed against the pinned
version rather than assumed.

**Concurrency-safe refresh**: Oda's token endpoint supports
`refresh_token` grants, and a rotating-refresh-token provider can invalidate
a token that a second concurrent request is mid-refresh with. Two household
members can plausibly trigger overlapping Oda calls (e.g. two people asking
about groceries within the same minute). `OdaTokenAuth` guards its refresh
with a module-level `asyncio.Lock` (single process, single household row —
no cross-process coordination needed, matching the rest of this design):

```python
_refresh_lock = asyncio.Lock()

async def _refresh(self) -> str:
    async with _refresh_lock:
        # Re-check expiry after acquiring the lock — another concurrent
        # request may have already refreshed while we were waiting.
        account = _load_account()
        if account.expires_at > _now() + timedelta(seconds=60):
            return account.access_token
        new_tokens = await _exchange_refresh_token(account.refresh_token)
        _persist_account(new_tokens)  # single UPDATE, still inside the lock
        return new_tokens.access_token
```

If no `IntegrationAccount` row exists yet (never connected), `start_mcp()`
behaves like Homey/Prometheus with no URL configured: log and return `None`,
Oda tools simply aren't registered. No crash, no retry storm.

## Policy gate additions (`app/policy/default_policies.py`)

`app/policy/gate.py`'s fallback only auto-allows tool names starting with
`get_/list_/search_`. Most Oda read tools don't match that prefix
(`similar_and_related_products`, `likely_to_buy`, `unique_for_you`,
`order_tracking`; `recipe_search` and everything `get_*` do match). Rather
than rely on the prefix heuristic, add explicit policies split by real
side effect, not just "can it spend money":

- **Auto-allow (low impact, `requires_confirm: False`)**: every read tool
  (`get_cart`, `get_delivery_addresses`, `get_delivery_slots`,
  `product_search`, `get_category`, `get_brand`,
  `similar_and_related_products`, `likely_to_buy`, `unique_for_you`,
  `recipe_search`, `get_liked_recipes`, `get_purchased_recipes`,
  `get_product_lists`, `get_dinner_lists`, `get_product_list`, `get_orders`,
  `get_order`, `order_tracking`) plus `feedback` — `feedback` only sends
  commentary about the MCP tools themselves (per Oda's own tool
  description, not store/delivery/complaints), so it has no household-visible
  effect and gating it would just be friction for no safety benefit.
- **Require confirmation (`requires_confirm: True`)**: `manipulate_cart` and
  `select_delivery_slot`. No payment is at stake, but both mutate a real
  shared household resource — a wrong `manipulate_cart` call (e.g.
  misreading "remove the pasta" and clearing the whole cart) or booking the
  wrong delivery slot costs real time and household friction to undo, even
  though neither spends money. This matches the conservative default the
  rest of the policy gate already applies to any unrecognized write tool,
  and is a deliberately easy value to loosen later via the existing
  `ActionPolicy` admin UI once real usage shows it's more annoying than
  useful for routine adds.

```python
[
    {
        "name": "Oda reads + feedback",
        "tool_pattern": "<one per read tool, or several policies>",
        "impact_level": "low",
        "requires_confirm": False,
    },
    {
        "name": "Oda cart/delivery writes",
        "tool_pattern": "manipulate_cart",  # + a second entry for select_delivery_slot
        "impact_level": "medium",
        "requires_confirm": True,
        "confirm_message": "Update the shared Oda cart?",  # per-tool message
    },
]
```

(`gate.py` uses `fnmatch`, not regex/alternation — the real entries need one
`tool_pattern` per tool, not the single combined pattern shown above; noted
here as intent, exact patterns are an implementation detail.)

If Oda later adds an order-placing tool, that must get its own
`requires_confirm: True` policy before the tool is registered — flag this
explicitly in the PR that eventually adds it.

## Data placement

Nothing new is written to the world model or episodic memory. Cart
contents, order history, delivery slots, and tracking status are all fetched
live per call — this is exactly the "operational/transient, not memory" case
`CLAUDE.md` already calls out. If a future iteration wants the agent to
remember household grocery preferences ("we always get oat milk, not
regular"), that's a normal episodic-memory candidate, but it's out of scope
here — the MCP tool itself already exposes `likely_to_buy`/`unique_for_you`
personalization from Oda's side.

## Feature flag

`feature_oda: bool` in `Settings`, no separate URL setting needed for the
MCP endpoint itself (fixed: `https://oda.com/mcp`) — registration in
`agent.py` is conditional on the flag **and** an existing
`IntegrationAccount(household_id=..., provider="oda")` row, same two-gate
pattern Homey/Prometheus already use (feature flag + "is it actually
configured").

## Agent behaviour (`prompts/instructions.md`)

Tool wiring alone doesn't tell the agent *how* to act around groceries.
Follow the existing `## Wine Cellar` precedent in `prompts/instructions.md`:
a compact, always-loaded decision-rules block, not an `app/skills/` entry
(skills are for lazy-loaded domain knowledge/APIs — the skills design doc
explicitly excludes tool workflows, that's MCP's job).

Drafted section content (kept intentionally short, matching the compact
style of the rest of `instructions.md`):

```markdown
## Groceries (Oda)

- When multiple options exist for a requested item, prefer the version
  previously bought (check `get_orders`/`get_purchased_recipes` or order
  history) over an arbitrary pick — e.g. same salami brand as last time.
- No budget constraints — do not filter or comment on price.
- If the cart already has an item of the requested type, say so before
  adding another.
```

Not included, deliberately: proactive suggestion behavior, dietary/allergy
cross-checks, cart-size confirmation heuristics, checkout-reminder cadence.
None of these were requested — add them later only if a real scenario comes
up, rather than pre-writing rules for hypothetical cases.

## Open questions

- **Token refresh cadence**: reactive (refresh on 401) is simplest and
  matches the design above; a proactive refresh-before-expiry background job
  is not needed unless Oda's access tokens turn out to be very short-lived.
  Not observable via a third-party MCP client (confirmed by asking) — decide
  once Phase 1's own token exchange shows real `expires_in` values.
- **Dynamic client registration persistence**: DCR happens once; if Oda ever
  invalidates the registered client, the connect flow needs to detect that
  (e.g. `invalid_client` from the token endpoint) and re-register rather
  than silently failing forever. Handle in the `connect`/refresh error path.

Resolved (see "Confirmed via live testing" above): cart deep link
(`get_cart`'s `url` field), `manipulate_cart` and `select_delivery_slot`
schemas, `feedback`'s rate limit.

## Implementation sequence (for when this moves to a plan)

1. **Done.** `IntegrationAccount` model (household-scoped, unique on
   `(household_id, provider)`) + `OAuthState` model + Alembic migration;
   derived-Fernet-key helper (not raw `app_secret_key`).
2. **Done.** `oda_oauth_public_base_url` setting.
3. **Done.** Oda OAuth client (`app/oda/oauth.py`): metadata discovery
   (RFC 9728/8414, not hardcoded beyond the fixed protected-resource URL),
   DCR, PKCE authorize URL builder, token exchange, single-flight refresh
   with `asyncio.Lock` (in `app/integrations/accounts.get_valid_access_token`,
   not in `oauth.py` itself — `oauth.py` stays pure HTTP, no DB access).
   Covered by `tests/unit/test_oda_oauth.py`,
   `tests/unit/test_integrations_accounts.py`,
   `tests/unit/test_integrations_crypto.py` (31 tests, all HTTP calls faked —
   nothing hits oda.com). One implementation deviation worth noting: DB
   `DateTime` columns round-trip as naive through SQLite regardless of what's
   written, so `get_valid_access_token`'s expiry comparisons use naive
   `datetime.utcnow()`, matching the existing convention in
   `app/policy/pending.py` rather than the timezone-aware `datetime.now(timezone.utc)`
   used elsewhere for write-only fields.
4. Admin `/admin/integrations` routes + dashboard page (generic, Oda is the
   first provider); `connect` writes `OAuthState`.
5. Public `/integrations/{provider}/callback` route on `app/api/server.py`:
   atomic state consume + validate, token exchange, `start_mcp()` +
   `reload_agent()` on success. Disconnect route mirrors with
   `stop_mcp()` + `reload_agent()`.
6. `app/oda/mcp_client.py` (MCP wiring + `OdaTokenAuth`, verified shape per
   the spike above).
7. `default_policies.py` entries — reads/`feedback` auto-allow,
   `manipulate_cart`/`select_delivery_slot` require confirmation;
   `feature_oda` flag + `agent.py` registration gated on flag + account row.
8. Targeted tests: policy evaluation for Oda tool names (both the allow and
   confirm sides), token-refresh `httpx.Auth` behavior under concurrent
   callers, connect/callback state validation (expired, replayed, mismatched
   `redirect_uri`), Fernet key derivation round-trip — no live calls to
   oda.com in unit tests.
