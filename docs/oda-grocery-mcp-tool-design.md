# Oda Grocery MCP Tool — Design

Status: All 5 phases implemented (OAuth + storage foundation; admin
Integrations page; public callback; MCP client + policy gate; agent
behaviour). Feature-complete pending a real household connecting an
account and exercising it in production.
Last code check: 2026-08-08
Implemented runtime entry points: `app/models/integrations.py`
(`IntegrationAccount`), `app/models/cache.py` (`OAuthState`),
`app/integrations/crypto.py` (Fernet key derivation), `app/integrations/accounts.py`
(household-scoped account repository + single-flight refresh),
`app/integrations/oauth_state.py` (one-time PKCE/state storage),
`app/oda/oauth.py` (OAuth client: discovery, PKCE, DCR, token
exchange/refresh/revoke), `app/oda/mcp_client.py` (MCP wiring:
`OdaTokenAuth`, policy-gated `process_tool_call`, `start_mcp`/`stop_mcp`),
`app/control/api.py` (`/admin/integrations` list/connect/disconnect routes,
now also gated on `feature_oda` and wired to stop/reload the agent on
disconnect), `app/control/dashboard.html` ("Integrations" tab),
`app/api/integrations.py` (public `/integrations/{provider}/callback`
route, now also starting/reloading the agent on success),
`app/policy/default_policies.py` (21 Oda tool policies), `app/agent/agent.py`
(Oda toolset assembly, two-gate: `feature_oda` + connected account),
`alembic/versions/0015_users_db_integration_account.py`,
`alembic/versions/0007_cache_db_oauth_state.py`,
`alembic/versions/0008_cache_db_oauth_state_client.py`,
`prompts/instructions.md` (`## Groceries (Oda)`, verified loaded via
`tests/unit/test_prompts_static_split.py`).

Nothing left to build. What's untested is real-world behavior: no household
has actually connected an Oda account yet, so the live token-exchange/
DCR/refresh calls, the real `manipulate_cart` argument shape in practice,
and the cart deep-link copy are all still only verified against Oda's
documented schemas and mocked responses — see "Open questions" below.

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

**New admin routes — implemented** (`app/control/api.py`, LAN-only port
9090, existing `dependencies=_auth`), covered by
`tests/unit/test_control_api_integrations.py` (FastAPI `TestClient` against
a real in-memory users.db/cache.db, all Oda network calls mocked):

- `GET /admin/integrations` — lists `_KNOWN_INTEGRATION_PROVIDERS` (just
  `"oda"` today — a plain `dict[str, str]` of provider → display name, not a
  full plugin registry, per "generalize minimally") with connection status
  (connected/not, connected-by display name, connected-at, token expiry),
  read-only.
- `POST /admin/integrations/{provider}/connect` — runs metadata discovery
  and dynamic client registration fresh on every call (see the `OAuthState`
  schema note above for why), generates PKCE verifier/challenge + `state`,
  stashes them via `app.integrations.oauth_state.save_state`, returns the
  provider's `authorize_url` for the admin browser to navigate to.
  Reconnecting after an existing connection is allowed — treated as a
  refresh/re-auth, not an error. Requires a `user_id` in the request body
  (which household member is connecting — audit trail only, matches the
  existing `_EventRuleBody.user_id` convention elsewhere in this file)
  rather than any notion of an "admin identity," since the admin API is a
  single shared bearer secret with no per-admin-user concept.
- `POST /admin/integrations/{provider}/disconnect` — best-effort revoke via
  `app.oda.oauth.revoke_token` (the design doc's original plan; revocation
  failure is logged and does **not** block deleting the local
  `IntegrationAccount` row — the household should always be able to forget
  a stored credential even if Oda's revoke endpoint is unreachable), deletes
  the row, then (Phase 4) calls `app.oda.mcp_client.stop_mcp()` and
  `app.agent.agent.reload_agent()` so the Oda toolset actually disappears
  from the running agent, not just the DB.
  Also now gated on `feature_oda` at the top of `connect` (not disconnect —
  disconnecting should always work regardless of flag state, tearing down
  an MCP connection is always safe/idempotent even if one happens to be
  running).

**`OAuthState` (`cache.db`)** — the stashed PKCE/state row, one-time and
short-lived (10 min TTL, matching the human-timescale of an OAuth consent
screen):

```text
state: str (pk)              # the "state" value itself
provider: str
household_id: str
initiating_user_id: str      # which admin session started this
pkce_verifier: str
redirect_uri: str            # exact URI used in the authorize request; the
                              # callback must reuse the identical value —
                              # OAuth token exchange requires an exact match
client_id: str                # from DCR — the callback needs the SAME
client_secret: str            # registered client to complete token exchange
created_at: datetime
expires_at: datetime
```

**Deviation from the original design**: `client_id`/`client_secret` were
added during Phase 2 implementation — the original schema above didn't
account for dynamic client registration happening at `connect` time (before
any `IntegrationAccount` row exists to store it in), so the callback would
have had no way to know which registered client to complete the token
exchange with. `client_secret` is Fernet-encrypted at rest via the same
`app.integrations.crypto` helper `IntegrationAccount` uses, even though this
row is short-lived — added via `alembic/versions/0008_cache_db_oauth_state_client.py`
on top of Phase 1's `0007_cache_db_oauth_state.py` rather than editing that
migration, per this repo's convention of always adding new migrations,
never amending old ones.

The callback handler consumes it atomically: read + delete in the same
step (`app.integrations.oauth_state.consume_state` — implemented, tested in
`tests/unit/test_oauth_state.py`), reject if missing/expired (replay or
stale link), and verify the `state` from the query string matches the row's
primary key before proceeding.

**New dashboard page — implemented**: an "Integrations" tab in
`app/control/dashboard.html` listing providers as cards (name, status badge,
Connect/Disconnect button, a household-member picker for who's connecting)
— same visual language as the existing tabs (proposal-card styling, `.badge`
classes, `.details-btn` buttons). Verified manually against the real dev
DB by running the admin router standalone (`GET /admin/integrations`,
`GET /admin/users`, and the connect/disconnect error paths — the actual
DCR/authorize network call was not exercised live, to avoid registering a
throwaway OAuth client against Oda's real server outside of a real user
flow).

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

**Flow — implemented** (`app/api/integrations.py`, registered in
`app/api/server.py`'s `create_app()`; tests in
`tests/unit/test_integrations_callback.py`, 8 cases, all Oda network calls
mocked):

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
   the **public** app. The handler: consumes the `OAuthState` row by
   `state` (atomic read+delete, `app.integrations.oauth_state.consume_state`
   — consumed even on a denied/errored consent, so nothing dangles until
   TTL expiry), rejects if missing/expired/provider-mismatched, exchanges
   `code` and `pkce_verifier` for tokens at `oda.com/o/token/` using the
   **exact** stored `redirect_uri` and the state row's `client_id`/`client_secret`
   from DCR, upserts the `IntegrationAccount` row (`household_id` and
   `connected_by_user_id` from the state row), emits an
   `integration.connected` control event, and shows a plain "Oda connected
   — you can close this tab" page. Any failure at any step renders a
   distinct, non-leaky error page (invalid link, denied consent, exchange
   failure) rather than a stack trace. If `feature_oda` is on, also calls
   `stop_mcp()` (tear down any stale connection from a previous reconnect)
   then `start_mcp()` then `reload_agent()`, so the running agent picks up
   the fresh Oda toolset immediately — no process restart needed.

Disconnect (Phase 2) does the mirror image: best-effort revoke, delete the
account, then `stop_mcp()` + `reload_agent()` unconditionally (tearing down
an MCP connection is always safe, regardless of `feature_oda`).

At process startup, `start_mcp()` checks for an existing `IntegrationAccount`
row the same way Homey/Prometheus check for a configured URL — no account,
no toolset, agent built without Oda tools, exactly as today for an
unconfigured Homey/Prometheus deployment. Also gated on `feature_oda` in
`app/api/server.py`'s lifespan — an operator can disable the tool without
disconnecting the account.

## MCP client wiring (`app/oda/mcp_client.py`) — implemented

Mirrors `app/homey/mcp_client.py`'s shape (module singleton, `start_mcp()` /
`stop_mcp()` with retry/backoff), with three differences from the original
plan below, discovered during implementation:

- No `get_mcp_toolset()` with simple/advanced schema filtering — Oda has no
  Homey-style meta-tool (`search_tools`/`use_tool`) pattern to filter down,
  it exposes ~21 named tools directly, so `get_mcp_server()` is returned
  as-is; the policy gate (not toolset filtering) is what controls risk.
- No `verify_after_write` scheduling. Homey's policy gate polls device
  state after a write because physical state can lag/fail silently; a
  grocery cart's `manipulate_cart` response already reflects the new state
  synchronously, so there's nothing to poll for.
- The concurrency-safe refresh lock lives in
  `app.integrations.accounts.get_valid_access_token` (built in Phase 1),
  not inside `OdaTokenAuth` itself — `OdaTokenAuth.async_auth_flow()` just
  calls that function before the first request attempt and again after a
  `401`, described below.

`OdaTokenAuth(httpx.Auth)` implements `async_auth_flow()`: attaches
`Authorization: Bearer <token>` from `get_valid_access_token(household_id,
"oda")` before the first attempt, and on a `401` response, calls it again
(triggering the single-flight refresh) and retries once.

**Verified against the installed `pydantic-ai==2.21.0`** (not just read from
signatures): `MCPToolset`'s `_build_transport()` treats a URL-shaped client
(`"https://oda.com/mcp"` qualifies) as needing an explicit HTTP transport
whenever `auth` is set, and passes it straight through to FastMCP's
`StreamableHttpTransport(url=..., auth=auth, ...)`. So
`MCPToolset(MCP_URL, auth=OdaTokenAuth(household_id), process_tool_call=...)`
is the right shape — confirmed against the pinned version, not just assumed.
Also confirmed empirically: `httpx.Auth.async_auth_flow`'s generator
protocol (`response = yield request`, `yield request` again to retry) works
exactly as documented — see `tests/unit/test_oda_mcp_client.py`'s
`test_refreshes_and_retries_once_on_401`.

If no `IntegrationAccount` row exists yet (never connected) — or no
household exists at all — `start_mcp()` behaves like Homey/Prometheus with
no URL configured: log and return `None`, Oda tools simply aren't
registered. No crash, no retry storm. The actual successful-connection path
(`MCPToolset.__aenter__` + `list_tools()` against a real/fake server) isn't
unit-tested, matching the existing (also untested) state of
`app.homey.mcp_client.start_mcp()`'s success path in this codebase.

## Policy gate additions (`app/policy/default_policies.py`) — implemented

`app/policy/gate.py`'s fallback only auto-allows tool names starting with
`get_/list_/search_`. Most Oda tools don't match that prefix — confirmed by
listing them out: `similar_and_related_products`, `likely_to_buy`,
`unique_for_you`, `order_tracking`, `recipe_search`, `manipulate_cart`,
`select_delivery_slot`, and `feedback` all fail the prefix check
(`recipe_search` *ends* with `_search`, it doesn't *start* with `search_` —
an earlier draft of this doc claimed it matched; it doesn't). Rather than
rely on the prefix heuristic, every single Oda tool gets an explicit entry,
split by real side effect, not just "can it spend money":

- **Auto-allow (low impact, `requires_confirm: False`)** — all 19 read
  tools plus `feedback` (`_ODA_AUTO_ALLOW_TOOLS` in
  `default_policies.py`) — `feedback` only sends commentary about the MCP
  tools themselves (per Oda's own tool description, not store/delivery/
  complaints), so it has no household-visible effect and gating it would
  just be friction for no safety benefit.
- **Require confirmation (`requires_confirm: True`)**: `manipulate_cart` and
  `select_delivery_slot`, each with its own non-generic `confirm_message`
  ("Update the shared Oda cart?" / "Book this Oda delivery slot?"). No
  payment is at stake, but both mutate a real shared household resource —
  a wrong `manipulate_cart` call (e.g. misreading "remove the pasta" and
  clearing the whole cart) or booking the wrong delivery slot costs real
  time and household friction to undo, even though neither spends money.
  This matches the conservative default the rest of the policy gate already
  applies to any unrecognized write tool, and is a deliberately easy value
  to loosen later via the existing `ActionPolicy` admin UI once real usage
  shows it's more annoying than useful for routine adds.

One implementation detail worth flagging: `gate.py`'s fallback confirm
message for an unmatched/empty-`confirm_message` policy is hardcoded
Homey-worded (`f"Execute '{tool_name}' on your Homey?"`). Giving every
single Oda tool an explicit entry with a real `confirm_message` avoids ever
hitting that fallback — `tests/unit/test_default_policies_oda.py` asserts
neither Oda confirm message contains the word "homey", specifically to
catch a future regression here.

Covered by `tests/unit/test_default_policies_oda.py` (28 tests: raw
`DEFAULT_POLICIES` content — every auto-allow tool has an entry, no
duplicate names, the two write tools aren't also in the auto-allow list —
plus behavioural checks through `evaluate_policy()` itself, seeded with the
real `DEFAULT_POLICIES` list sorted the same way the production DB query
orders it).

If Oda later adds an order-placing tool, that must get its own
`requires_confirm: True` policy before the tool is registered — flag this
explicitly in the PR that eventually adds it.

## Feature flag and agent wiring — implemented

`feature_oda: bool = False` and `oda_tool_timeout_secs: int = 15` in
`Settings` (`app/config.py`). Two-gate registration in
`app/agent/agent.py`'s `_make_conversation_agent()`: the Oda toolset is
attached only when `settings.feature_oda` is true **and**
`app.oda.mcp_client.get_mcp_server()` returns a live connection (which is
already `None` if the household never connected) — an operator can disable
the tool without disconnecting the account. Covered by
`tests/unit/test_agent_oda_toolset.py` (comparing toolset counts
with/without a connected server present, since `Agent.toolsets` wraps
everything in framework-internal objects and always includes its own
function-toolset — absolute counts/identity checks aren't meaningful).

`app/api/server.py`'s lifespan starts/stops the Oda MCP client alongside
Homey/Prometheus/tools (start gated on `feature_oda`; stop unconditional,
same reasoning as disconnect above), and logs its status in the existing
`"MCP startup: homey=... prom=... tools=... oda=..."` line.

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

## Agent behaviour (`prompts/instructions.md`) — implemented

Tool wiring alone doesn't tell the agent *how* to act around groceries.
Follows the existing `## Wine Cellar` precedent in `prompts/instructions.md`:
a compact, always-loaded decision-rules block, not an `app/skills/` entry
(skills are for lazy-loaded domain knowledge/APIs — the skills design doc
explicitly excludes tool workflows, that's MCP's job). Inserted directly
after the `## Wine Cellar` section. Presence verified by
`tests/unit/test_prompts_static_split.py::test_static_prompt_body_contains_groceries_section`
(part of the existing byte-identical/no-placeholder static-body test suite,
so a future edit that breaks the `{{...}}` JSON-escaping pass or introduces
an identity placeholder here would already be caught).

Final section content (kept intentionally short, matching the compact
style of the rest of `instructions.md` — exactly what was requested, no
more):

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
4. **Done.** Admin `/admin/integrations` routes + dashboard page (generic,
   Oda is the first provider); `connect` writes `OAuthState` (now including
   `client_id`/`client_secret` from DCR — see the `OAuthState` deviation
   note above). Added `app/oda/oauth.revoke_token` (RFC 7009) to support
   `disconnect`, which wasn't in the original Phase 1 scope. Covered by
   `tests/unit/test_oauth_state.py`,
   `tests/unit/test_control_api_integrations.py`, and 5 new
   `TestRevokeToken` cases in `tests/unit/test_oda_oauth.py` (39 tests
   total for this phase). Manually verified against the real dev DB by
   running `app.control.api`'s router standalone on a throwaway port — page
   load, `GET /admin/integrations`, `GET /admin/users`, and all three error
   paths (unknown provider, missing `ODA_OAUTH_PUBLIC_BASE_URL`, disconnect
   when not connected) — without exercising the real DCR/authorize network
   call.
5. **Done.** Public `/integrations/{provider}/callback` route
   (`app/api/integrations.py`, registered in `app/api/server.py`): atomic
   state consume + validate, token exchange, `IntegrationAccount` creation,
   `integration.connected` control event. Covered by
   `tests/unit/test_integrations_callback.py` (10 cases after step 6 added
   two more for the lifecycle wiring). Manually verified error paths against
   the real dev DB (invalid/unknown state, unknown provider) by running the
   router standalone; the real token-exchange call to oda.com was not
   exercised live.
6. **Done.** `app/oda/mcp_client.py` (MCP wiring + `OdaTokenAuth`, verified
   shape per the spike in step 3, confirmed empirically here too). Closed
   out both deferred lifecycle TODOs: the callback (step 5) now calls
   `stop_mcp()` + `start_mcp()` + `reload_agent()` on success when
   `feature_oda` is on, and `admin_disconnect_integration` (step 4) now
   calls `stop_mcp()` + `reload_agent()` unconditionally. Also retroactively
   added a `feature_oda` check to `admin_connect_integration` (step 4 didn't
   have one — the flag didn't exist yet), so a disabled deployment can't
   start a connect flow that would just sit unusable. 13 new tests in
   `tests/unit/test_oda_mcp_client.py` (OdaTokenAuth's auth flow —
   attach/no-token/401-retry — `_resolve_household_id`, and the
   policy-gated `process_tool_call`'s auto-allow/truncate/timeout/
   confirm-required/incomplete-deps paths).
7. **Done.** `default_policies.py` entries — reads/`feedback` auto-allow,
   `manipulate_cart`/`select_delivery_slot` require confirmation with
   distinct messages; `feature_oda` flag + `agent.py` registration gated on
   flag + account row (`tests/unit/test_agent_oda_toolset.py`).
8. **Done**, spread across each step above rather than as a separate pass:
   policy evaluation for Oda tool names (`test_default_policies_oda.py`,
   28 cases), token-refresh behavior (`test_oda_mcp_client.py`,
   Phase 1's `test_integrations_accounts.py` for the single-flight lock
   itself), connect/callback state validation
   (`test_integrations_callback.py`), Fernet key derivation round-trip
   (`test_integrations_crypto.py`, Phase 1). 111 Oda-related tests total
   across all 5 phases (110 from Phases 1–4 plus the Phase 5 static-prompt
   presence check), all HTTP calls faked — no live calls to oda.com in any
   unit test.
9. **Done.** `## Groceries (Oda)` in `prompts/instructions.md` (Phase 5) —
   see "Agent behaviour" above.

## Production rollout checklist

1. `uv run alembic upgrade heads` — applies the three new migrations
   (`0015_users` `IntegrationAccount`, `0007_cache`/`0008_cache`
   `OAuthState`). Additive only, no destructive changes to existing tables.
2. Set `ODA_OAUTH_PUBLIC_BASE_URL` to the real public HTTPS hostname
   (the Cloudflare Tunnel domain — must match what oda.com redirects back
   to).
3. Set `FEATURE_ODA=true`.
4. Restart the app so the new settings take effect.
5. Open the admin dashboard's **Integrations** tab, pick which household
   member is connecting, click **Connect**, approve on oda.com.
6. Watch for the "Oda connected" page and the `integration.connected`
   event in the admin Live feed; `GET /admin/integrations` should then show
   `connected: true`.
7. Ask the agent to do something Oda-related (e.g. "what's in our Oda
   cart?") and confirm the tool call actually reaches Oda — this is the
   first real exercise of the live token-exchange/refresh path, which has
   only been verified against mocks and Oda's documented schemas until now.
8. Try a `manipulate_cart` request specifically, to confirm the real
   confirmation-prompt flow (Telegram inline Yes/No) works end-to-end, not
   just in the unit tests.
