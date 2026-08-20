# Household Identity & Access Design

Status: problem definition + goals/scenarios captured — no decisions made
yet. Options, tradeoffs, and a phased plan follow in a later pass once this
framing has been reviewed.
Last code check: 2026-08-20
Related docs: `docs/user-identity-memory-link-design.md` (identity↔memory
link, predates web chat), `docs/web-chat-channel-design.md` (Decision #1
and "Explicitly Deferred" — the PIN and `telegram_id`-optional items this
doc absorbs and widens), `docs/oda-grocery-mcp-tool-design.md` (a third,
different authorization model), `docs/control-loop-admin-tab-design.md`,
`docs/architecture.md` ("Channel and intake boundaries")

## Purpose

HomeAgent now has three surfaces a household member or operator can reach
it through — Telegram, the web chat channel, and the admin dashboard — plus
a fourth, narrower case (Oda) where a household authorizes an external
service. Each was built at a different time, against a different immediate
need, and each independently answered the questions "who is this," "how do
they prove it," and "what are they allowed to do." Nobody has yet asked
those three questions once, for the household as a whole, across every
current and near-future surface.

This document exists to write down, plainly, what's actually true about
identity and access today, why the gap has become a real practical problem
rather than a theoretical one, and what questions the next design pass
needs to answer. It deliberately does not propose a solution yet.

## Why now

Three things converged in the last few days of testing and made this
un-ignorable rather than a nice-to-have:

1. **The web chat login picker only shows 2 of 4 household members.**
   Traced to root cause: a `User` row (the only thing web chat's login
   screen can offer) is created exactly one way in the entire codebase —
   `app/bot.py`'s auto-create-on-first-Telegram-message path, keyed by a
   required, unique `User.telegram_id`. The world model already knows
   about all 4 people (`HouseholdMember`, which has always had a nullable
   `user_id` "for children/guests" — this was anticipated, just never
   built out). Two of the four have never texted the Telegram bot, so they
   have no login identity anywhere in the system — not "no Telegram on
   this device," but no account at all. Web chat was pitched as "a
   household member without Telegram installed can still talk to the
   agent," but that only holds if they already have a `User` row from
   using Telegram elsewhere first. For a genuinely Telegram-less member
   (a kid, a guest, a partner who just doesn't want another app) it
   doesn't hold at all.
2. **The request to make web chat installable as a PWA and reachable from
   outside the LAN.** Both were named as follow-up asks in this same
   conversation. Today's entire web chat trust model is "you're physically
   on the home network, so we don't ask for a password" — stated
   explicitly and deliberately in `docs/web-chat-channel-design.md`
   ("this is intentionally weaker than password auth... appropriate
   because the trust boundary is the LAN"). Remote access over a published
   Cloudflare hostname removes that boundary entirely. Anyone who
   discovers the URL would be able to pick any household member from the
   list and act as them — chat with their memory/context, and trigger real
   actions (Homey control, an Oda basket) gated only by the policy-gate
   confirmation, not by anything that verifies they *are* that person.
   PWA installability compounds this differently: an installed PWA holds
   a long-lived session token on a device that can be lost, borrowed, or
   shared in a way a browser tab typically isn't.
3. **Two prior decisions already punted on exactly this**, in different
   contexts, and both are now directly in the way:
   - `docs/user-identity-memory-link-design.md` (2026-06-26, before web
     chat existed) lists "No replacement of Telegram as the primary user
     channel" as an explicit Non-Goal, and states "`User.telegram_id`
     remains required for Telegram-backed users." Reasonable at the time —
     there was one household member using the system.
   - `docs/web-chat-channel-design.md` (Phase 0, 2026-08-05) deferred both
     "web-only users with no `telegram_id`" and "per-user PIN" to an
     unscheduled Phase 2, calling the schema change "a real need
     eventually" with "ripple effects elsewhere."

   Both deferrals were reasonable narrow scoping calls at the time. Taken
   together now, they mean the identity model has never actually been
   designed for a multi-member household using more than one channel —
   it's grown by accretion, one channel's immediate need at a time.

## Current state — what's actually true today

Stated precisely, because the inconsistency is easy to understate in the
abstract:

**Identity storage.** Two tables already model "a person," imperfectly
bridged, and a third concept — session/credential — isn't modeled as a
first-class thing at all yet:
- `User` (users.db) — the *authentication/account* identity.
  `telegram_id: int` required and unique. `is_admin: bool`. `preferred_channel:
  str = "telegram"` (field exists, not meaningfully read anywhere yet).
  Created exactly once in the codebase, at `app/bot.py`'s Telegram
  auto-create path.
- `HouseholdMember` (world model, users.db) — the *household-knowledge*
  identity. Has interests, goals, activities, routines, aliases.
  `user_id: Optional[str]`, explicitly nullable "for children/guests" —
  i.e. a person can be known to the agent without ever being able to log
  in anywhere.
- `ChannelMapping` (users.db) — a generic `(channel, channel_user_id) ->
  user_id` bridge table, already used for both `telegram` and `email`.
  This part is already channel-agnostic and reasonably well designed; the
  bottleneck is entirely upstream of it, at `User` creation.

Three different things are in play here, and the goals and scope
below only make sense if they stay distinct: **a person** (may not be
represented by any row at all yet), **an account** (`User` — login
capability; a `HouseholdMember` does not need one just to be known to the
agent), and **a session/credential** (a signed-in device or browser
install — today only `WebChatSession`, one row per active web login,
whose `token` is stored as plaintext as its own primary key in `cache.db`
— a real gap in its own right, see Scope below). Conflating "give every
household member a login" with "every household member must be
represented" is exactly the trap to avoid — a toddler should stay a
`HouseholdMember` with no `User` at all, on purpose, not as an oversight.

**Authentication, per surface — four different answers to "how do you
prove who you are":**
- *Telegram*: allowlist (`ALLOWED_TELEGRAM_IDS`) plus Telegram's own
  account security. Strong in practice (a real phone-linked account), but
  entirely outside HomeAgent's control.
- *Web chat*: no password. Pick a name from a list, get a signed-nothing
  opaque bearer token with a 30-day sliding expiry. Explicitly
  LAN-trust-only by design (see above).
- *Admin dashboard*: a single shared secret (`APP_SECRET_KEY`) via
  `require_admin_auth` — not per-user at all. `User.is_admin` is checked
  nowhere in `app/control/`; it only gates which *Telegram slash commands*
  a user can run (`app/commands/dispatcher.py`). A household member with
  `is_admin=True` and the person holding the admin bearer token are
  answerable to two completely disconnected questions.
- *Email*: no authentication of the sender at all — trust is placed
  entirely in the existing `ChannelMapping(channel="email")`, which can
  only be created by an already-Telegram-authenticated user via a slash
  command. Explicitly documented as deliberate: "email sender address
  alone is not a strong authentication factor."
- *Oda (OAuth integration)*: different shape again —
  `IntegrationAccount` is one row per `(household_id, provider)`, not per
  user. Whoever completes the OAuth handshake authorizes the credential
  for the whole household; `connected_by_user_id` is audit metadata only,
  never checked at tool-call time.

**Authorization / what you're allowed to do.** Once identified, the
policy gate (`app/policy/gate.py`, `ActionPolicy`) is consistent across
*channels* — impact-level-based confirmation requirements apply the same
way regardless of which channel a message came in on (`AgentDeps.channel`,
added when web chat shipped). It is not, however, consistent across
*people*: `ActionPolicy` has no per-person dimension at all — no role, no
user reference, nothing. A child, a guest, and an adult who all hold a
`User` account get byte-identical policy treatment for the same tool call
today, because the concept of "this action requires a certain kind of
person" doesn't exist anywhere in the model. That's a separate gap from
the channel-consistency one, and the identity model has to decide whether
it stays that way (household-wide policy, no per-person authorization) or
gets at least a role dimension — see Goals and Scope below.

Separately: a "Confirm" tap is only as trustworthy as the session behind
it — today identical whether that session was established on the LAN five
minutes ago or over the internet thirty days ago. Whether some impact
levels should require a *recent* reauthentication (step-up), not just any
valid session, is a real open question once "valid session" can mean
"authenticated from anywhere, a month ago" — a question for the next pass,
not something the impact-level model itself needs to change to answer.

## The core tension this doc needs to hold

Every one of today's weak-auth decisions (no web chat password, shared
admin secret, unauthenticated email sender) was individually reasonable
given its stated trust boundary: *you have to already be on the home
network, or already be a Telegram user with real account security, to
reach any of it.* The new asks — PWA install, and reachability from
outside the LAN via Cloudflare — don't just add a feature, they remove the
one assumption every existing decision was leaning on. This can't be
solved by patching web chat's login screen in isolation; the same question
("is LAN-only trust still the right default anywhere, now that at least
one surface won't be LAN-only?") touches admin and, to a lesser extent,
the whole channel model.

Note precedent already exists for the *mechanics* of exposing a local
service publicly: `CLOUDFLARE_TUNNEL_TOKEN` already routes the main
webhook app (port 8080) to a public hostname today. The gap isn't
"how do we get a public URL" — that's solved — it's "what stands at that
public URL to make it safe to have one." A tunnel is transport, not
identity: "reachable via Cloudflare" must never be treated as a
substitute for actually authenticating whoever is on the other end of the
connection.

## Goals

What "good" looks like, in priority order. These are what the next pass's
options get judged against — not a checklist to satisfy equally.

1. **Security proportionate to reach, not uniformly maximal.** A LAN-only
   surface and an internet-reachable one don't need identical defenses,
   but the level for each has to be a deliberate choice, not an inherited
   accident of "this is what was easiest to build for the first channel."
2. **Owning a login must never depend on which channel you happened to use
   first.** Today it depends entirely on Telegram. The goal isn't "every
   household member gets an account" — some (young children, short-term
   guests) should stay `HouseholdMember`-only, on purpose. The goal is
   that *when* someone should have login access, getting it doesn't
   require them to already own a Telegram account. This is the one that
   unblocks the concrete gap that started this doc.
3. **One account principal per person, everywhere.** A member's history,
   memory, and permissions must be the same regardless of which channel or
   device they're on — no silent duplicate accounts, no detached
   histories. This doesn't mean every household member needs an account
   (see Goal 2), and it doesn't mean personal data must be exposed
   uniformly across every channel — just that if two accounts exist for
   the same person, that's a bug, not a feature. Household-confirmed
   target, not an open question: this is where the model is meant to
   land.
4. **Per-user, per-surface access control, managed from an admin UI.**
   Household-stated requirement, distinct from the broader "does
   authorization need role tiers" question in Foundational Decision 2
   below: independent of any role system, the admin wants to grant or deny
   a specific person access to a specific surface — this person can use
   web chat but not Telegram, this one has admin access and that one
   doesn't — from a dedicated admin screen, not by editing `.env` or
   database rows by hand. The capability is decided; the mechanism (most
   likely a `User` × surface permission matrix) is for the next pass.
5. **Controlling which surfaces are reachable beyond the LAN should be an
   easily changeable setting, not a deployment-time decision baked into
   `docker-compose.yml`.** Household-stated requirement: e.g. publish web
   chat's PWA-facing API via Cloudflare while keeping the admin dashboard
   LAN-only, and be able to change that later without hand-editing compose
   files or `cloudflared` config. Concretely, this points at a new admin
   "Channels" capability, per surface. What exactly that capability
   changes — real network exposure, or only what HomeAgent's own app-level
   gate accepts — is a genuinely open mechanism question, not decided
   here; see Foundational Decision 3.
6. **Low friction for the people actually using this day to day.**
   This is a household of a few people, not an org with an IT desk.
   Whatever onboarding/login flow comes out of this must stay usable by
   a kid and by a non-technical adult without hand-holding, every time —
   not just on a good day.
7. **Individually revocable, and actually revoked.** Losing a phone with
   an installed PWA, or a guest device that was once trusted, must be
   recoverable without resetting everyone else's access. Concretely, that
   means: session tokens stored as a hash server-side (not the case
   today — `WebChatSession.token` is the literal bearer credential, in
   plaintext, as its own primary key); an absolute maximum session
   lifetime in addition to sliding idle expiry (today: sliding only —
   30 days of any activity at all is, in practice, close to indefinite); a
   real `revoked_at` / "revoke this session" / "revoke all sessions"
   model; logout / switch-user actually clearing local credentials and
   cached chat content — including PWA/service-worker caches, which must
   not surface a previous user's messages to whoever opens the app next;
   and this extends to Goal 4's access toggles and Foundational Decision
   2's roles too — a role or surface-access change must take effect
   immediately, not whenever a long-lived session claim next happens to
   expire. That means checking authorization live per request, or at
   minimum a cheap invalidation signal (e.g. an `authorization_version`
   compared against the session on each use) rather than baking permissions
   into the session token at issuance and trusting it for 30 days.
8. **Auditable, durably.** The admin should be able to see who is (or
   recently was) connected, from where, on which channel, via a
   persistent record of logins, session creation, channel-linking,
   revokes, and admin changes — not just observe them live. Correcting an
   earlier draft of this doc: `app/control/events.py`'s event bus is *not*
   that record — it's an in-memory ring buffer capped at 150 events, gone
   on restart. A real audit trail needs its own durable storage, a
   retention policy, and care about what "from where" means: IP/address
   data is itself sensitive, and if it's ever derived from
   `X-Forwarded-For`/`CF-Connecting-IP`-style headers, those must only be
   trusted when they come from an explicitly configured, trusted reverse
   proxy — never taken at face value from the request.
9. **Sustainable for a household of one admin.** Whatever this becomes,
   Kristian is the entire ops team. A solution that requires ongoing
   manual account/credential management doesn't survive contact with
   actual use.

## Scenarios to support

Concrete cases the next pass's design needs to hold up against — written
the way `docs/web-chat-channel-design.md`'s "Target Experience" section
did, because abstract requirements are easy to satisfy on paper and fail
on the first real case.

1. **A household member with no account anywhere** (today: 2 of the 4
   `HouseholdMember` rows) picks up a tablet or phone and wants to talk to
   HomeAgent for the first time, ever, with no prior Telegram history.
   Today: impossible — they don't even appear in the web chat picker.
2. **That same person later gets Telegram** (a kid getting their first
   phone, say) — arguably the hardest scenario here, harder than any web
   login case. Messaging the bot for the first time must link to their
   *existing* identity and history, not silently create a second, detached
   one — but it must be *proven*, not inferred: matching on name or
   shared-household-membership alone is exactly the failure mode to avoid
   (it's how one household member ends up able to claim another's
   history). The next pass needs to pick one concrete linking mechanism —
   an admin-issued linking code, a one-time invitation, or an
   already-logged-in web session explicitly approving the incoming
   Telegram link — not leave it implicit.
3. **An existing Telegram user opens web chat for the first time.** Already
   works today — must keep working exactly the same after any redesign.
4. **A family member checks in from outside the LAN** — a parent glancing
   at reminders from work, a kid doing homework at a relative's house.
   They need to actually *be authenticated*, not merely "have found the
   URL."
5. **Someone who is not a household member finds the public URL** (a
   leaked link, a guessed hostname, a scan). Two failures stack today: the
   picker would let them select "Mom" and chat as her with zero further
   checks, but even before that — the picker itself hands the full
   household member list to anyone who loads the page, which is
   information disclosure regardless of whether login then succeeds. A
   remote entry point shouldn't show *any* identities until after
   authentication. The LAN can still reasonably keep today's low-friction
   picker-first flow — but that's an accepted trusted-LAN risk being taken
   deliberately, not a claim that LAN equals physical presence. A guest on
   the Wi-Fi password, a compromised IoT device, or a misconfigured
   network can reach the LAN too; "on the network" is a weaker signal than
   the current design's framing implies, it's just a signal this household
   has chosen to accept for the LAN case specifically.
6. **A member installs the web chat PWA on their personal phone.** The
   session persists across app opens (that's the point of installing it),
   but if the phone is later lost or stolen, that one installation's
   access needs to be killable without touching anyone else's.
7. **Someone else picks up a shared device** (the kitchen tablet) that's
   mid-session as a different family member. "Switch user" needs to stay
   as frictionless as it is today for this case specifically — this is
   the scenario most at risk of an overcorrection toward heavier auth
   making the LAN/shared-device experience worse than it is now.
8. **The admin wants to see who currently has access, and revoke one
   person's without affecting others** — no such view exists today for
   web chat sessions (only a live connection *count*, per-session
   management isn't there) and doesn't exist at all for the conceptual
   question "who can log in as whom."
9. **The admin wants to grant one household member web chat access but
   not Telegram, or vice versa, or pull admin access from someone who had
   it** — no such control exists today at all; the only per-user knob is
   `is_admin`, and nothing governs per-surface access. This is Goal 4's
   scenario: a permission-matrix view in the admin UI, not a config file
   edit.
10. **The admin wants web chat's PWA-facing API reachable from the internet
    via Cloudflare while keeping the admin dashboard LAN-only — and to
    change that decision again next month without hand-editing
    `docker-compose.yml` or `cloudflared` config.** No such control exists
    today; this is entirely a deployment-time, file-editing decision. This
    is Goal 5's scenario, and the reason Foundational Decision 3 exists:
    whatever the admin does on that "Channels" page has to be honest about
    whether it actually changed the network path or just what HomeAgent
    itself chooses to accept on it — the admin needs to know which one
    they just did.
11. **The admin dashboard itself is opened remotely.** Same tension as web
    chat, but for a surface that can directly rewrite policy, event rules,
    and world-model data — arguably deserves a *higher* bar than chat, not
    the same one.
12. **The admin loses access** — forgets or loses whatever credential
    guards `/admin/*`, or the one `is_admin` account gets locked out.
    Today: reset `APP_SECRET_KEY` in `.env` and restart, because admin
    auth isn't tied to any account at all. Any redesign that ties admin to
    a real per-user account needs its own break-glass recovery path, and
    needs to define how the *first* admin account gets established on a
    fresh deployment in the first place (today: implicit, via
    `ADMIN_TELEGRAM_IDS` in `.env` plus whoever messages first).
13. **A second household forms** (out of scope per this doc's own
    Non-Goals, but worth naming so the chosen model doesn't accidentally
    make it harder later) — not a scenario to design for now, but one the
    next pass's model shouldn't foreclose without noticing it's doing so.

## Foundational decisions for the next pass

Three questions shape most of the rest, and are worth deciding first and
deliberately rather than letting them fall out as a side effect of
whichever scope bullet gets tackled first.

**1. Exposure, perimeter, and internal identity are three separate axes,
not one either/or choice.** An earlier draft of this doc framed "auth
inside HomeAgent" and "identity-aware perimeter" as alternatives; they
aren't — a perimeter decides who gets to *knock*, HomeAgent still has to
decide *who's knocking* and map them to a `User`, and that has to work for
LAN and shared devices regardless of what stands in front of it remotely.
The next pass should decide each axis on its own terms:

- **Exposure**: LAN-only, VPN, or a public Cloudflare tunnel — per
  surface (web chat and admin don't have to land on the same answer).
- **Perimeter control**: none, or an identity-aware proxy (Cloudflare
  Access or equivalent) gating access before HomeAgent is ever reached.
- **Internal identity**: how HomeAgent itself authenticates a request and
  maps it to a `User`, independent of whatever perimeter sits in front of
  it — this still has to exist even behind a strong perimeter, and still
  has to work for the LAN/shared-device case where there's no perimeter
  at all.

  Within that third axis: strength should scale with reach, not be one
  flat answer. A short PIN is reasonable for a LAN/shared tablet, or as a
  step-up on top of an existing session — it is not, on its own, adequate
  standalone security for a public URL. Anything reachable from the
  internet needs at least a passkey or a strong identity-aware perimeter
  in front of it — not a PIN doing that job alone. An invitation
  credential, if used, is a *bootstrap* mechanism, not an ongoing one: a
  short-lived, one-time enrollment secret that's spent establishing a
  passkey or other durable credential, not something that keeps working as
  a repeatable login method afterward.

**2. Does authorization need person-level roles, or does the household
stay flat?** Worth being precise about what's already decided vs. still
open here, since Goal 4 above settles part of this space: *which surfaces
a person can use at all* (web chat / Telegram / admin) is a confirmed
requirement — a per-user, per-surface toggle, not a role hierarchy. What's
still genuinely open is narrower and downstream of the gap named in
Current State → Authorization: once someone's in a given surface, do a
child, a guest, and an adult need *different* agent / Homey / Oda action
permissions, or is "admin" vs. "everyone else" (today's only real
distinction, and only for Telegram slash commands) sufficient? If action-
level roles are needed on top of the surface toggles, the minimum shape is
probably `admin`, `adult`/`member`, and `restricted`/`guest` — but that's
a next-pass decision, not a foreclosed one. This choice touches the `User`
model, onboarding (what role does a new account default to, and who can
grant a higher one), session claims (what a session needs to carry to make
this checkable), and policy evaluation (`ActionPolicy` gains a role
dimension or it doesn't) — and interacts with Goal 4's surface toggles: a
`restricted` role plus no Telegram access, say, is a materially different
risk profile than a flat model where every account with any surface access
is equally privileged everywhere.

**3. Does the admin "Channels" capability (Goal 5) actually change network
exposure, or only control which incoming traffic HomeAgent's own app-level
gate accepts?** These are materially different capabilities, and
conflating them is a mistake worth naming explicitly, because an earlier
draft of this doc made exactly that mistake — calling a statically-tunneled
port plus an app-level toggle "LAN-only." It isn't: if the tunnel routes
to that port, the endpoint is network-reachable from the internet
regardless of what the application then chooses to do with the request.
That's an access-control decision, not a change in exposure — a materially
different security posture (the port is still directly reachable by
anything that can find it) than genuinely not routing traffic there at
all.

The two real options:

- **HomeAgent controls actual exposure** — manages Cloudflare Tunnel's
  public hostnames at runtime. This is its own separate, privileged
  integration: API credentials, a readback step to confirm a change
  actually took effect (not just that the API call succeeded), its own
  audit trail, and defined fail-closed behavior if the integration itself
  breaks. Substantially larger scope than anything else in this document.
- **HomeAgent controls only its own acceptance** — the network path stays
  exactly as deployed; HomeAgent decides, per request, whether to process
  it. This requires a genuinely trusted signal of how the request arrived
  — `Host` header or request origin alone are not reliable proof of
  arrival path (both are attacker-influenceable or ambiguous depending on
  network topology); a trusted ingress marker set only by the real ingress
  path, validation of a Cloudflare Access token, or a dedicated
  listener/port per trust level are the credible options. And this
  doesn't change the underlying exposure fact from the point above — the
  endpoint remains reachable, the application just declines to answer.

Whichever this becomes, the resulting admin UI has to show three things
*separately*, never collapsed into one toggle: the household's **desired**
policy, the **actual** perimeter (which HomeAgent can only partially know
— it has no way to confirm whether a VPN is active upstream, for
instance, so this field has to be honest about being observed/configured,
not verified), and HomeAgent's **own** access-gate decision.

Not a decision this document makes, but one informed opinion worth
recording for the next pass to weigh: build the app-level-acceptance
version first, label it plainly as an **access policy**, not
**exposure**, and show perimeter status as a separate, clearly-caveated
field rather than implying HomeAgent controls it. The exposure-managing
version is a legitimate later option, not a default to design toward now.

## Scope for the next (design/options) pass

In scope — this is meant to be the wide framing the household asked for,
not a narrow re-run of the telegram_id migration:

- A single identity model: how `User`, `HouseholdMember`, and
  `ChannelMapping` relate once login-without-Telegram is possible — do
  they merge, or does the split stay but get a real onboarding path that
  doesn't require `User` to exist first? Whatever shape it takes, it
  should be expressible as concrete integrity constraints, not just prose
  — at minimum: `HouseholdMember.user_id` is null or unique (one account
  per person, not shared); one `User` maps to at most one household
  member; `ChannelMapping`'s `(channel, channel_user_id)` uniqueness
  (already true today) is preserved; linking a new channel identity and
  issuing a session both happen transactionally, not as separate steps
  that can partially fail; and — restated from Scenario 2 because it's
  worth repeating as a hard rule, not a preference — no automatic merging
  of accounts based on matching name, email, or any other soft attribute.
- Authorization per person, not just per channel, at two levels that
  shouldn't be conflated: (1) **surface access** — can this person use web
  chat / Telegram / admin at all — is a confirmed requirement (Goal 4),
  needing a `User` × surface permission matrix and an admin UI to manage
  it, most likely a new tab or section on the existing admin dashboard
  rather than a separate app; (2) **action-level roles** — does an
  authenticated child have different Homey/Oda permissions than an adult —
  remains genuinely open (Foundational Decision 2), and whether
  `ActionPolicy` gains a role dimension or the household deliberately
  stays flat there is a separate, still-undecided question.
- What's authoritative for Telegram access specifically, once Goal 4's
  admin-managed surface toggle exists alongside today's static
  `ALLOWED_TELEGRAM_IDS` env var — the next pass has to pick one
  relationship between them, not leave both live and potentially
  disagreeing. One shape worth weighing: env stays a bootstrap/hard
  ceiling (nobody gets in who isn't listed there, full stop — a
  deploy-time safety net), while the database becomes the runtime
  authority for the finer-grained per-user toggle within that ceiling.
- Authentication strength appropriate to each trust boundary: LAN-only web
  chat vs. remotely-reachable web chat vs. admin dashboard vs. PWA-at-rest
  — these may reasonably end up with different answers, but the answers
  need to be chosen on purpose, not inherited from whichever was easiest
  to build first.
- Onboarding flow: who can create a new household member's login (self-
  service invite? admin-provisioned? first-touch-any-channel auto-create,
  generalized past Telegram?), and how it interacts with `HouseholdMember`
  rows that predate any login (children/guests today).
- Admin access: whether `User.is_admin` should become the actual gate for
  `/admin/*` (replacing or supplementing the shared secret) — and treat
  "should admin ever be remotely reachable at all" as its own explicit
  decision, not an inherited variant of whatever web chat decides. Admin
  can rewrite policy, event rules, and world-model data directly, a
  materially higher blast radius than chat; a reasonable starting default
  is that it stays LAN/VPN-only, or sits behind an identity-aware proxy in
  addition to its own auth, rather than getting a direct public hostname
  the way chat might.
- Multichannel consistency: one session/identity model household members
  experience the same way regardless of channel, vs. deliberately
  different postures per channel (and if the latter, on what basis).
- Remote access posture specifically: concrete choices along the three
  axes in "Foundational decisions" above (exposure / perimeter / internal
  identity), per surface — plus rate limiting and lockout behavior once
  brute-forcing the picker is a realistic threat rather than a
  theoretical one.
- An admin "Channels" page (Goal 5, Scenario 10, Foundational Decision 3):
  per-surface visibility into desired policy, observed/configured
  perimeter, and HomeAgent's own access-gate decision — shown as three
  separate things, not one status. What the page's controls actually
  change (real network exposure vs. only HomeAgent's acceptance) is the
  open mechanism question in Foundational Decision 3, not decided here.
- Web session security minimums — not optional hardening to consider
  later, a fixed bar the next pass's design has to clear: `HttpOnly` +
  `Secure` + an appropriate `SameSite` on any session cookie; no bearer
  token readable by JS (today's implementation fails this twice —
  `chat.html` keeps the token in `localStorage`, and passes it via
  `?token=` in the WebSocket URL, both readable/leakable in ways an
  `HttpOnly` cookie isn't); Origin checking on the WebSocket handshake;
  CSRF protection on session and admin mutations if cookies replace
  bearer tokens (the current bearer-header design is CSRF-resistant by
  construction, but loses that property the moment it moves to cookies —
  the two choices have to be made together, not separately); a real CSP
  as XSS defense, since XSS against any of these surfaces is equivalent to
  impersonating whoever's logged in, auth model notwithstanding; and an
  active WebSocket connection closing immediately on revoke, not drifting
  along until its next natural disconnect.
- Step-up reauthentication: whether some `ActionPolicy` impact levels
  should require a *recent* login, not just a currently-valid session,
  once "valid session" can mean "authenticated from anywhere, a month
  ago" (see Current State → Authorization above).
- Durable audit storage: where it actually lives (own table? something
  alongside `AgentRunLog`'s pattern?), retention period, and — since IP/
  address data is itself sensitive — that any "from where" capture only
  trusts proxy headers from an explicitly configured, trusted proxy.
- Email's role, bounded explicitly: not usable for login, account
  recovery, channel-linking, or high-impact confirmation without a new,
  separately-designed strong verification step. Today's admin-initiated
  `ChannelMapping(channel="email")` is sound for its current narrow
  purpose (flight-booking forwards, etc.) and should stay exactly that
  narrow — not grow into an identity proof by accretion the way Telegram
  did.
- Migration and recovery strategy: idempotent pre-provisioning of
  existing `HouseholdMember` rows into `User` accounts where appropriate
  (never inferred by name-matching — see Scenario 2), an account
  disable/lock state, a break-glass recovery path for a locked-out admin,
  and how the *first* admin account gets established on a fresh
  deployment (see Scenario 12). Break-glass specifically has to stay
  local/operator-controlled (physical or LAN access to the machine, e.g.
  today's "edit `.env`, restart" shape) and generate an audit entry when
  used — a recovery path that's itself remotely triggerable, or that's
  quietly exempt from the durable audit log in Goal 8, would undermine
  everything else here. Email must not become a recovery channel later by
  default, for the same reason it's excluded from login and
  channel-linking above. One explicit invariant the next pass should hold
  regardless of mechanism: it must never be possible, through the admin UI
  or otherwise, to disable the last remaining administrator, or the last
  remaining working recovery method — the system should refuse that action
  outright rather than let an admin lock the household out by mistake.
- PWA specifics: manifest/icons/install-prompt is mechanical and
  low-risk; the actual design question is what a long-lived credential
  sitting on an installable, possibly-shared device implies for session
  lifetime, revocation, and the "switch user" affordance that already
  exists (see Goal 7 for the concrete session-lifecycle requirements this
  implies).
- How this reconciles with Oda's already-shipped household-wide (not
  per-user) authorization model. Household-wide credential ownership is
  the right call to keep — Oda has no concept of "which family member" on
  their end, and there's nothing to gain by pretending otherwise.
  `connected_by_user_id` should stay "who authorized this integration,"
  never "who owns the grocery account." What the next pass should add:
  every individual Oda tool call (not just the initial connection) gets
  an identified actor in the audit log — the household owns the
  credential, but each action taken with it still traces back to a
  person.

## Explicitly not in scope for this document

- No decisions, options comparison, or implementation plan — that's the
  next pass, informed by this one.
- No re-litigation of the policy gate's *mechanics* — impact levels,
  confirmation prompts, the `ActionPolicy` matching logic — which already
  work consistently across channels and aren't broken. Whether it should
  gain a per-person role dimension is explicitly in scope (Foundational
  Decision 2); how impact-level matching itself works is not.
- No multi-household support (still one household per deployment, per
  `docs/user-identity-memory-link-design.md`'s existing Non-Goals, which
  this doc doesn't challenge).
