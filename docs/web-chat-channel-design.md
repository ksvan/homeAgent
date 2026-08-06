# Web Chat Channel Design

Status: implemented (Phases 1a–1c) — Phase 2 items remain explicitly deferred
Last code check: 2026-08-06
Runtime entry points: `app/webchat/app.py` (standalone FastAPI app, own port),
`app/webchat/api.py` (routes + WebSocket), `app/webchat/channel.py`
(`WebChannel`), `app/webchat/session.py`, `app/webchat/dispatch.py`,
`app/policy/confirm.py` (shared with Telegram), `app/models/cache.py`
(`WebChatSession`), `app/channels/registry.py` (multi-channel registry)

## Purpose

Give household members a local, browser-based way to talk to HomeAgent —
functionally a new channel alongside Telegram and email, not a new product.

Same agent, same context assembly, same conversation continuity, same policy
gate. The web app is a thin, well-designed front door onto the existing
single-agent runtime, reached from any device on the home network.

It should feel like starting a chat with a present, capable household
assistant — not like operating a settings panel, and not like a generic
chatbot playground. It is explicitly **not** the admin dashboard: no logs, no
policy tables, no system internals. Just conversation, clearly presented as
being with an agent/robot rather than a person.

## Non-Goals (v1)

- No password-based accounts, invites, or self-registration. Login is
  "pick yourself from the household's existing user list."
- No public internet exposure. LAN-only, same trust model as the admin app.
- No new conversation-history concept. The web channel shares the same
  per-user conversation continuity as Telegram/email — see "Continuity"
  below.
- No redesign of the agent into a multi-agent or per-channel-context system.
  `assemble_context()` stays user-scoped, not channel-scoped.
- No rebuild of confirmation/policy semantics — the web channel reuses the
  existing policy gate and confirmation flow, with a web-native UI for it.

## Target Experience

A household member on the home Wi-Fi opens `http://<host>:<port>/chat` (or a
bookmarked PWA icon) on a phone, tablet, or laptop.

1. **Who's talking?** A simple screen: avatars/names of the household's
   existing users (from `User`), tap to select. No password. Optionally a
   short PIN per user later (see Phase 2), but v1 is trust-the-LAN, same as
   picking your name off a fridge whiteboard.
2. **Chat.** A clean message thread — agent messages visually distinct
   (avatar/icon that reads as "robot," e.g. a simple mark, not a photo of a
   person) from the user's own messages. Typing indicator while the agent is
   assembling context / calling tools, since some tool calls (Homey, search)
   take a few seconds.
3. **Continuity.** If Kristian asks Telegram "remind me to call the plumber"
   and later opens the web app, the agent already has that context — same
   `user_id`, same conversation history, same world model. The web app is
   just another window into the same ongoing relationship, not a fresh bot.
4. **Confirmations.** High-impact actions (per the policy gate) render as
   inline Yes/No buttons in the thread — the web-native equivalent of
   Telegram's inline confirmation buttons — not a redirect to another
   channel.
5. **Presence cue.** Small, persistent visual signal that this is an AI
   agent with real capabilities (tool use, home control) — e.g. a subtle
   "thinking" / "using a tool" state shown inline ("checking the
   thermostat…") rather than a bare spinner. This is a trust/legibility
   feature: family members should be able to tell when the agent is *doing*
   something in the house versus just chatting.
6. **Multi-user awareness.** If another household member is also chatting
   concurrently, no cross-talk — each user's thread is their own (same as
   Telegram DMs today). No shared "family room" view in v1.

## Architecture Fit

### Channel abstraction (reuse, don't fork)

`app/channels/base.py` already defines the `Channel` contract every adapter
implements: `send_message`, `send_confirmation_prompt`,
`send_email_intake_prompt`. `docs/architecture.md` already lists
`WebChannel(Channel)` as a planned future implementer — this design fulfills
that.

`WebChannel` becomes a real implementation:

- Inbound: a FastAPI router (WebSocket or SSE-for-out + POST-for-in) accepts
  a message from an authenticated browser session, builds an
  `IncomingMessage(channel="web", channel_user_id=<user.id>, text=..., raw=...)`,
  and calls into the same dispatcher path `app/bot.py` already uses for
  Telegram (rate limiting → user lookup → slash-command routing →
  `agent_run()` → `save_message_pair`). No parallel pipeline.
- Outbound: `send_message` pushes to the browser over the open
  WebSocket/SSE connection for that session; `send_confirmation_prompt`
  sends a structured payload the frontend renders as buttons instead of
  plain text.
- Because a browser session isn't a stable external ID the way a Telegram
  chat_id is, `channel_user_id` for the web channel should be a **server-
  issued session ID**, not the human-picked `User.id` directly — see Identity
  below. `ChannelMapping(channel="web", channel_user_id=<session_id>)` still
  fits the existing generic table.

### Identity & login (no new auth stack)

Today `User.telegram_id` is a required, non-nullable, unique field —
Telegram is baked into core identity, not cleanly channel-generic yet. Two
options:

- **Recommended for v1:** don't touch `User` schema. The web login screen
  lists existing `User` rows (name + avatar) scoped to the household(s)
  configured for this instance. Selecting a user creates a short-lived
  signed session (see below) bound to that `user_id`. This works today with
  zero migration, and matches "pick from current users."
- **Deferred:** loosen `telegram_id` to optional to support web-only
  household members with no Telegram account at all. This is a real need
  eventually (e.g. a kid with no Telegram) but is a schema/migration change
  with ripple effects elsewhere (`app/bot.py` auto-create logic assumes
  Telegram) — explicitly out of scope for v1, called out as a Phase 2+ item.

No JWT/OAuth library is warranted. Precedent in this repo
(`app/control/auth.py`) is a bearer-token check against `settings.app_secret_key`.
For the web chat channel: on user selection, issue a signed, expiring session
cookie (HMAC over `user_id` + expiry using `app_secret_key`, or a random
opaque token stored server-side in `cache.db` — cache.db is explicitly the
right layer per `CLAUDE.md` for "operational runtime state"). No password.
Session expiry (e.g. 30 days, sliding) balances "family member doesn't want
to re-pick every visit" against not being permanently open on a shared
device. A "not you? switch user" affordance handles shared devices (family
tablet on the kitchen wall).

This is intentionally weaker than password auth. It's appropriate because
the trust boundary is the LAN/home network, same as the current admin app
and Telegram-bot-in-a-private-chat model — not a new, weaker boundary
relative to what exists today.

### Context & continuity

No changes needed. `assemble_context(user_id, household_id, current_text)`
in `app/agent/context.py` is already keyed by `user_id`, not by channel.
Conversation history (`app/memory/conversation.py`) is already
channel-agnostic. A user's web chat and Telegram chat are, correctly, the
*same conversation* from the agent's point of view. This is a feature, not a
gap to fix — it's what makes the web app feel like "the same agent," not a
second bot.

The one piece of new state is **connection-level**, not conversation-level:
which browser session is currently live, for routing outbound
pushes/confirmations. That belongs in the web channel adapter / `cache.db`,
never in conversation history or the world model.

### Serving pattern

**Decided (see Decisions #2/#3 below):** a third small FastAPI service, its
own port, separate from both the main app and admin app:

- The admin app (`settings.admin_port`, default 9090) is control/observability
  — stays exactly as-is, not touched.
- The main app (`settings.port`, default 8080) keeps webhooks/API — not
  touched either.
- The web chat UI gets its own `settings.web_chat_port` and its own small
  FastAPI app (same process/container as `homeagent`, not a `services/*-mcp`
  sidecar, since it needs direct DB/agent access the way the admin app
  does). This keeps the household-facing surface's lifecycle, auth model
  (per-user session vs. admin bearer token vs. none on the main app), and
  blast radius independent from the other two. Deployment topology becomes
  two containers / three ports; `docs/architecture.md` needs updating once
  this ships.
- Streaming: **WebSocket**, not SSE. Latency matters more for a live chat
  thread than for the admin event stream's periodic updates, and a
  WebSocket avoids the SSE `?token=`-in-URL auth workaround (SSE's
  `EventSource` can't set headers) — auth token passed once at connect
  time instead. `app/control/admin_events.py`/`event_bus.py`'s SSE pattern
  remains the right fit for the admin dashboard and is not being replaced
  there, just not reused here.

### Frontend

A separate, small static app (own directory, e.g. `app/control/` sibling
such as `app/webchat/` for templates/static assets, or a lightweight
build-free HTML+JS page like `dashboard.html` already demonstrates this repo
is comfortable with). No new frontend framework/build pipeline unless there's
a concrete reason — the admin dashboard precedent (plain HTML/JS/CSS, no
bundler) is the right bar for v1. Should be installable as a PWA (manifest +
icon) so it can sit on a phone home screen like a real app, since that's core
to "family members use this daily."

Visual design should clearly signal "this is an agent," not mimic a human
messaging app 1:1 (e.g. no read receipts implying a person is watching,
avoid implying always-on human-like presence). A distinct agent avatar/mark,
a name (reuse whatever persona name `prompts/persona.md` already establishes
if any), and visible tool-use states are the key differentiators from a
generic chat UI.

## Feature Flag

Follow the `feature_email_channel` precedent exactly
(`app/config.py`, plain `Settings` field, not mirrored into the smaller
`FeatureFlags` model):

```python
feature_web_chat: bool = False
web_chat_port: int = 9091
```

`FEATURE_WEB_CHAT` (and `WEB_CHAT_PORT`) documented in `.env.example` next to
the other channel flags. The web chat FastAPI app is its own app instance
(see Serving pattern — own port, not mounted on `app/api/server.py`), wired
up in `app/__main__.py` alongside the admin app, started/stopped only when
`feature_web_chat` is on — same conditional-wiring shape as email's flag,
different mount point.

## Use Cases Covered (v1)

- Household member without Telegram installed on the current device (e.g.
  using a shared kitchen tablet) can still talk to the agent.
- Continuing a conversation started on Telegram from a browser (or vice
  versa) with full context.
- Confirming/declining a high-impact action from the web UI.
- Multiple household members using the web app concurrently from different
  devices without cross-talk.
- Quickly switching "who's talking" on a shared device.

## Explicitly Deferred

- Per-user PIN/passcode on top of user selection (Phase 2+, cheap to add
  once session infra exists).
- Web-only users with no `telegram_id` (schema change, Phase 2+).
- Rich media in the web UI (image upload, voice input) — mirror however
  Telegram attachments are handled once the text path is solid; not
  blocking v1.
- Push notifications to the browser when the agent proactively reaches out
  (scheduled prompts, reminders) — v1 web chat is pull/session-based only;
  proactive reach-out keeps going through Telegram until this is designed.
- Shared/family-room view (multiple users in one visible thread).
- Remote (non-LAN) access / auth hardening for exposing this outside the
  home network.

## Phased Implementation

Mirrors the phasing used for `docs/email-channel-agentmail-design.md`.
Phases 1a–1c are implemented; Phase 2 stays explicitly deferred (see below).

**Phase 0 — Spike / decisions — done.**
Serving pattern, transport, and trust-model decisions settled — see
Decisions above (own service/port, WebSocket, LAN + pick-a-user with
per-device remembered pick). Session mechanism resolved as part of Phase 1a
(below): opaque bearer token in `cache.db`, not a signed cookie — simpler,
easy to revoke server-side (`DELETE /api/session`), and the WebSocket
connection needs a token to authenticate at connect time regardless, so a
cookie would have bought nothing extra. No UI mock was produced separately
from the implementation — the sci-fi/clean direction from Decision #4 went
straight into `app/webchat/static/chat.html`.

**Phase 1a — Skeleton behind `feature_web_chat` — done.**

- `WebChannel(Channel)` in `app/webchat/channel.py` — `send_message`,
  `send_confirmation_prompt`, `send_email_intake_prompt`, all pushing JSON
  frames over whichever WebSocket is currently registered for a session
  token (best-effort — see class docstring on why offline sends are
  dropped, not queued).
- Session issuance: `POST /api/session {user_id}` → opaque token
  (`app/webchat/session.py`, `WebChatSession` table, alembic
  `0006_cache_db_webchat_session`). `GET /api/users` lists the household for
  the picker; no auth on that endpoint (LAN trust, matches the design).
- Static chat page (`app/webchat/static/chat.html`) — login picker with
  per-device remembered-user pre-highlight (`localStorage`), message
  thread, reconnect/backoff WebSocket client (pulled forward from Phase 1c
  since it's naturally part of the same client code).
- `feature_web_chat: bool = False` / `web_chat_port: int = 9091` in
  `app/config.py`; flag-gated everywhere.

**Phase 1b — Full path — done.**

- `app/webchat/dispatch.py` mirrors `app.bot.handle_incoming_message`'s
  shape (rate limit → slash command → `agent_run` → `save_message_pair`)
  rather than literally routing through `app/bot.py` — that module is
  Telegram-`int`-shaped throughout (auto-create-by-`telegram_id`, etc.);
  email doesn't reuse it either. Both channels converge on the same
  `agent_run()` / `save_message_pair()` core, which is the part that
  actually matters for "one runtime path."
- WebSocket push for agent responses + a tool-in-progress indicator: the
  WS handler subscribes to the existing `app.control.events` bus
  (the same one the admin dashboard's SSE stream reads), filters by
  `run_id`, and forwards `run.tool_call` events as `{"type": "status", ...}`
  frames — no new event plumbing needed.
- Confirmation prompts: `app/policy/confirm.py` extracts the
  ownership-check / MCP-execute / verify-schedule / conversation-bookkeeping
  core that used to live only in `TelegramChannel._execute_confirmed_action`
  so both Telegram (inline buttons) and web chat (`{"type":"confirm"}` /
  `{"type":"cancel"}` WS messages) drive the identical flow — each channel
  only renders the result in its own UI idiom.
- Required extending `app/channels/registry.py` to a real multi-channel
  registry (`register_channel(name, channel)` / `get_channel(name)`) and
  adding `AgentDeps.channel` / `agent_run(channel=...)`, since the previous
  single-global-channel model would have misrouted a web session's
  mid-run confirmation prompt to Telegram. See "Channel and intake
  boundaries" in `docs/architecture.md`.
- This is the "live" milestone — a household member can have a full
  conversation, including a confirmed high-impact action, entirely from the
  browser.

**Phase 1c — Hardening — done.**

- Session expiry/renewal: sliding TTL (`web_chat_session_ttl_days`,
  default 30), extended on each authenticated call/message
  (`touch_session`).
- "Switch user": `DELETE /api/session` + frontend clears the stored token
  and returns to the picker.
- Reconnect/backoff: exponential backoff (1s → 15s cap) in
  `chat.html`'s WebSocket client, done as part of Phase 1a's client code.
- Rate limiting: `app/bot.py`'s sliding-window limiter was generalized to
  accept `int | str` keys and is reused directly (keyed by `User.id`) —
  same algorithm, independent state per channel, no duplicated logic.
- Admin visibility: `WebChannel.active_connection_count()` surfaced via
  `/admin/stats.web_chat.active_sessions` and a "Web sessions" tile on the
  admin dashboard's control-loop tab; `webchat.session_started` /
  `webchat.session_ended` emitted via the existing `emit_admin_event`.

**Phase 2 — Polish / deferred items — still deferred, not implemented.**
PWA packaging (manifest, icons, install prompt), optional per-user PIN,
richer tool-in-progress UI, revisit web-only users (`telegram_id` optional)
if real demand appears, revisit push notifications for proactive
agent-initiated messages.

## Decisions (Phase 0 review)

1. **Trust model:** Leave LAN-only + pick-a-user as-is for v1, PIN stays
   deferred (no change to Explicitly Deferred). One addition: the browser
   should remember the last-picked user *for that browser/device* across
   sessions (e.g. a long-lived, non-sensitive local-storage hint — "last
   used: Kristian" — pre-selects the avatar on next visit, distinct from the
   actual signed session cookie/token that grants access). This is a UX
   convenience, not an auth mechanism — the signed session is still what
   authorizes the chat; the remembered pick just saves a tap. "Switch user"
   (already planned for Phase 1c) overrides it per-device.
2. **Transport:** WebSocket, not SSE-out+POST-in. Reasoning above (design
   doc originally leaned SSE by precedent) is overridden because latency
   matters more for a live chat thread than for the admin event stream, and
   the two surfaces are different channels serving different needs — no
   reason to force them onto the same pattern. Bidirectional WS also avoids
   the awkward SSE-can't-set-headers `?token=` workaround for auth (a
   `Sec-WebSocket-Protocol` header or a short-lived query-param token at
   connect time works, and the connection is already stateful per session).
   Phase 1a/1b tasks below updated accordingly.
3. **Serving pattern:** Reversed from the original recommendation — run web
   chat as its **own small FastAPI service** (own process, own port, own
   config), not mounted on the main app (port 8080) or the admin app （port
   9090). Rationale: keeps the household-facing surface's lifecycle,
   auth model, and blast radius fully independent from both webhook
   ingestion and admin/control internals — a crash or restart in one
   doesn't affect the others, and firewall/router docs get one clean new
   port to document rather than a shared one with mixed trust levels.
   Matches the two-container deployment's existing comfort with dedicated
   services (`services/*-mcp/`) — this one lives in-process with the main
   `homeagent` container (not a sidecar, since it needs direct DB/agent
   access like the admin app does) but as a separate FastAPI app + port,
   the same shape as admin already uses relative to the main app. New
   settings: `feature_web_chat: bool = False`, `web_chat_port: int` (new
   field alongside `port`/`admin_port` in `app/config.py`).
4. **Visual design:** No existing persona visual identity to reuse (`prompts/persona.md`
   defines tone/voice only, no visual branding). Proposed direction: sci-fi,
   robotic, modern, clean — same design language as the admin dashboard so
   the household's two custom surfaces feel like one product family, but
   distinctly *household-facing* (friendlier, simpler chrome, no data
   tables). Priorities: easy to use, clean, universally accessible (works
   across common browsers — no bleeding-edge CSS/JS dependent on one
   engine), no build pipeline (matches `dashboard.html` precedent).
5. **Proactive/scheduled messages → which channel:** Genuinely open, needs a
   follow-up design pass rather than a one-line answer. Options to evaluate
   next round, expanding on the ones raised:
   - **A. Fully separate** — proactive/scheduled always goes to Telegram
     only (today's behavior unchanged); web chat is pull/session-only
     forever. Simplest, but stops being true "same agent, any window" once
     a household member goes web-only.
   - **B. Full duplication** — every proactive message is sent to *all*
     channels the user has active (Telegram + web if both configured).
     Simple mental model, but risks double-notification annoyance and
     doesn't handle the user replying differently in two places at once.
   - **C. Fan-out + convergence** — proactive message is delivered to every
     channel the user has, but once the user responds/acts in one channel
     (e.g. confirms a reminder in web chat), the corresponding
     prompt/confirmation in the other channel(s) is marked resolved/stale
     (edited or suppressed) so there's no dangling duplicate to act on
     twice. More correct than B, more implementation work (needs a
     cross-channel "this PendingAction/prompt was already handled"
     signal — `cache.db` is the natural place, keyed by whatever
     correlates the fan-out, e.g. task/prompt id).
   - **D. Per-user preferred channel** — each `User` gets a configurable
     "primary channel for proactive reach-out" (defaults to Telegram if
     they have it, web if they don't); scheduled prompts/reminders always
     go there, chat continuity still works both ways for anything
     *user-initiated*. Closest to "user chooses which one to activate and
     continue from" from the raw feedback. Requires a small schema/config
     addition (preferred-channel field) but no fan-out/dedup complexity.
   - **Web-only users:** all options must handle a user with no
     `telegram_id` at all (today `User.telegram_id` is required — this is
     already called out as a Phase 2+ schema change in Identity & login
     above). Options A and D degrade cleanly (proactive just goes to
     whichever channel(s) exist); B/C need "which channels does this user
     actually have" resolved per-send regardless.
   - No connection assumed to the deferred "push notifications to the
     browser" item — that's about the browser being closed/backgrounded,
     this is about which channel the *content* targets. They compound: even
     with an answer here, a web-only proactive message still needs an open
     session or (eventually) a push notification to actually reach the
     user in real time.
   - Recommendation for the next pass: prototype **D** first (least new
     infra, matches user's own instinct about "choosing" a channel), keep
     **C**'s dedup mechanism in mind as a Phase 2 upgrade if households
     turn out to actively use both channels enough that duplication (B)
     or channel-blindness (A/D) both feel wrong in practice.
