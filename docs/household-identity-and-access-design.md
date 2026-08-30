# Household Identity & Access Design

Status: problem definition done; two of three Foundational Decisions
resolved by household input (see "Decisions" below); options/tradeoffs for
what remains open follow in this same pass. A phased implementation plan
comes after those options are reviewed.
Last code check: 2026-08-29
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
5. **Which surfaces are reachable beyond the LAN, and how.** Settled by
   the Decisions below for the two surfaces that exist today: web chat
   gets a real public hostname via the same Cloudflare Tunnel already
   proven for Telegram (a static, occasionally-hand-edited config change —
   the household accepted the same "as easy as Telegram already is" bar
   rather than insisting on a live toggle for the network path itself);
   admin stays LAN/VPN-only, full stop. The "easily changeable without a
   redeploy" aspiration this goal originally named lives on, just
   relocated to where it actually matters day to day: Goal 4's per-user
   surface-access toggles, not the network path underneath them. A live
   admin "Channels" exposure page (Foundational Decision 3's fuller
   machinery) remains a legitimate later addition if a third surface or a
   change of heart on admin's posture ever needs it — not required now.
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
10. **The admin wants web chat's PWA-facing API reachable from the
    internet via Cloudflare while keeping the admin dashboard LAN-only.**
    Resolved by the Decisions below — this is now exactly what happens,
    via the same static Cloudflare Tunnel mechanism already proven for
    Telegram. What's still a live, recurring need day to day isn't
    changing that network path (settled, rarely revisited, same as
    Telegram's own setup) but Scenario 9's finer-grained cousin: revoking
    or granting one specific person's access to a specific surface
    without touching the network config at all — that's Goal 4's
    permission matrix, not this scenario's concern.
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
gate accepts?** *Resolved for web chat, still open for admin* — see
Decisions below: web chat gets a real, statically-configured public
hostname (genuine exposure, not an app-level toggle pretending otherwise),
secured by uniform passkey login rather than by distinguishing arrival
path. Admin isn't going public at all, so this question simply doesn't
bind for it yet; the reasoning below stays relevant if that ever changes.
These are materially different capabilities, and
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

## Decisions (household input, 2026-08-29)

Direct answers to the three Foundational Decisions above, from Kristian.
These are decisions, not leanings — the options analysis below builds on
top of them rather than re-opening them.

**Foundational Decision 2 is resolved: stay flat.** No action-level role
tiers. `ActionPolicy` does not gain a role dimension — a child's account
and an adult's account get identical Homey/Oda action permissions once
they're authenticated, exactly like today. `admin` vs. everyone-else
remains the only distinction, and it stays scoped to what it already
governs (Telegram slash commands today, arguably `/admin/*` after this
pass — see the Admin access scope bullet below) rather than expanding into
a broader RBAC system. This does not touch Goal 4: per-user, per-*surface*
access (can this person use web chat / Telegram / admin at all) remains a
separate, confirmed requirement, orthogonal to this decision.

**Foundational Decision 1's exposure axis, revised same day: web chat
will be published externally the same way Telegram already is —
superseding the VPN-first framing below it in this section's edit
history.** The household has a VPN already, but the stated preference is
to reuse the exact infrastructure already proven for Telegram rather than
require a VPN client on every family member's device: the Cloudflare
Tunnel container already running (already carrying `TELEGRAM_WEBHOOK_URL`
to port 8080) gets a second public hostname routed to web chat's port
(9091), added the same static way Telegram's route was. No new
infrastructure, no runtime-managed Cloudflare API integration — literally
the same mechanism, one more entry.

That reuse is genuinely simple, but it does **not** carry over Telegram's
actual security property, which is worth being precise about: Telegram's
public endpoint is safe not because of Cloudflare, but because (a)
Telegram's own platform has already authenticated the human before the
message reaches HomeAgent at all, and (b) `TELEGRAM_WEBHOOK_SECRET`
proves the request came from Telegram's servers specifically. Web chat
has no equivalent upstream identity provider. So the household also
decided the load-bearing consequence of this reuse: **web chat's login
must become real, per-person authentication — a WebAuthn passkey per
household member** — replacing today's no-password picker as the actual
security boundary, applied the same way regardless of whether the request
arrives from the LAN or the public hostname. (A useful side effect: this
removes the need for Foundational Decision 3's trickier "trusted ingress
marker to tell LAN from remote apart" machinery for *this* surface —
there's no LAN-special-case to protect once the same strong login applies
everywhere.)

**The admin dashboard explicitly does not follow web chat onto the public
path.** It stays LAN/VPN-only — the VPN the household already runs is
still exactly the right tool for admin specifically, given admin can
directly rewrite policy, event rules, and world-model data (materially
higher blast radius than chat, per the existing Admin access scope
bullet). Foundational Decision 3's fuller exposure/perimeter/access-gate
framework remains fully open and un-decided for admin, simply because
admin isn't going public and the question doesn't bind for it yet.

**Both currently account-less household members should get full
accounts** — not a future-proofing exercise, a live v1 requirement. Given
web chat is now genuinely public rather than VPN-gated, onboarding has to
be admin-provisioned rather than open self-registration (see Option A
below, which changed as a direct result of this decision) — the
onboarding/linking mechanism (Scenario 1 for the first login, Scenario 2
for later Telegram linking) is the centerpiece of the options below, more
so now than before.

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
- A live admin "Channels" exposure page (Goal 5, Scenario 10,
  Foundational Decision 3) is **not required for this pass** — web
  chat's and admin's network paths are both decided and static, matching
  Telegram's own precedent. The three-way desired-policy /
  observed-perimeter / access-gate distinction Foundational Decision 3
  describes stays relevant if a live exposure-toggle UI is ever actually
  built later (e.g. for a third surface, or if admin's posture is
  revisited) — worth keeping the reasoning rather than deleting it, but
  nothing here blocks the current plan.
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

## Options for the next pass

Concrete alternatives for what the Decisions section left open, with a
recommendation named for each — recommendations, not decisions; the
household still chooses. A phased implementation plan follows once these
are picked.

### A. Onboarding mechanism (Scenario 1 — first login with no account anywhere)

Three shapes, in increasing order of friction/control:

- **A1 — Admin-provisioned, invite link.** Admin picks the
  `HouseholdMember` from an admin UI, clicks "create login," gets a
  short-lived one-time invite URL to hand to that person. Opening it
  starts passkey registration bound to that specific pre-created `User`
  row — the invite token is what proves "you're the person meant to claim
  this account," spent the moment the passkey ceremony completes, per
  Foundational Decision 1's "invitation is a bootstrap, not a standing
  credential." Full admin control, small amount of admin legwork per new
  person.
- **A2 — Self-service request, admin approves.** Anyone reaching web chat
  can hit "I'm new," enter a name; creates a pending request, admin
  approves (Telegram notification or admin dashboard), approval sends the
  same kind of invite as A1 to complete passkey registration. More moving
  parts (a pending-request state machine) for a household of a handful of
  people — probably more process than the problem needs.
- ~~A3 — Open self-register, no admin step~~ **— ruled out by the
  Decisions above, not just deprioritized.** This option's entire
  justification was "network-level access already gates who can create an
  account" (true for Telegram's allowlist, and would have been true for
  VPN-gated web chat). It stops being true the moment web chat is
  published the same way Telegram is: with no admin step and no VPN gate,
  "self-register by picking a name" on a genuinely public endpoint means
  an attacker can register the passkey for "Mom" before Mom does. Once
  the account creation event *is* the security-critical moment (which it
  now is, since it's when a passkey gets bound to an identity), it can't
  be the same event as "anyone who reaches the URL types a name."

**Recommendation: A1.** Directly follows from the public-web-chat
decision, not a preference reversal on its own terms — the invite-link
step exists specifically to be the thing that verifies "this real
person, and not whoever else found the URL, is who's registering this
passkey." A2's extra approval step doesn't add meaningful safety over A1
once the invite link itself is the security boundary; it only adds
process. Note this also gives Kristian a clean, auditable enrollment
record for free — see the device-enrollment note under Option D below.

### B. Linking a later-acquired Telegram account to an existing web-only `User` (Scenario 2)

Not really an alternatives question — Foundational Decision 1 and the
integrity constraints in Scope already settled the shape: never infer
from name. One mechanism, regardless of which onboarding option (A) is
picked: a short-lived, single-use linking code generated for a specific
existing `User.id` (via admin UI, or self-service from an already-
logged-in web session — either works, pick whichever reuses more of A's
UI), sent to the person out of band, entered via a new `/link <code>`
Telegram command. The bot verifies the code against that specific
`User.id` and creates `ChannelMapping(channel="telegram",
channel_user_id=str(telegram_id))` — never touching `User.name` matching
logic at all. Small, contained addition to `app/commands/handlers.py`.

### C. Does web chat's login need to get stronger, now that it's public? — resolved

This was written as an open fork (C1 no change / C2 step-up only / C3
PIN-or-passkey at every login) back when the working assumption was a
VPN-gated web chat, where "no change" was a defensible answer. It no
longer is one: the Decisions section above settled this — web chat is
published the same way Telegram is, and the household chose **passkey
per person, applied uniformly** (closest to what was labeled C3 here,
minus the PIN alternative — a PIN was already ruled inadequate standalone
security for a public URL back in Foundational Decision 1). What's still
a genuinely open implementation question, not reopened by this: whether
any action ever needs *step-up* on top of the standard passkey session
(the old C2 idea) — plausible for something like a large Oda order or an
away-from-home Homey action, but worth building only if a concrete case
asks for it, not speculatively.

### D. Session security hardening (Goals 7 and 8, concrete shape)

Less a fork, more a specific implementation, since the Goals already
dictated most of it. Worth naming one simplification the current
architecture already provides: `WebChatSession` validation is already a
live DB lookup on every request (no in-memory caching of session validity)
— so "authorization changes take effect immediately" (Goal 7) doesn't
need a separate `authorization_version` invalidation scheme *as long as*
Goal 4's per-surface access check is implemented the same way (checked
live per request/message, not baked into the token at issuance). The
concrete schema change: `WebChatSession` gains `token_hash` (the actual
bearer value is shown once at issuance and never stored — lookups compare
hashes, matching the earlier review's requirement), an
`absolute_expires_at` set once at creation and never extended by
`touch_session` (alongside the existing sliding `expires_at`), and
`revoked_at` for the explicit revoke model. `User` gains the per-surface
flags Goal 4 needs (or a small side table if that reads cleaner —
implementation detail for the plan, not this pass).

**This is also, for free, the device-enrollment story Option C's passkey
decision raises.** A passkey is inherently device-bound (platform
authenticator or synced keychain), so registering one during an A1 invite
already *is* enrolling that device; no separate enrollment system is
needed. The resulting `WebChatSession` — once hardened as above — becomes
the durable, per-device enrollment record: Goal 8's admin visibility (see
who's connected) and Scenario 8's per-session revoke (kill one lost
phone's access without touching anyone else's) apply directly, with
nothing extra to build. A heavier option exists — Cloudflare Access with
WARP device posture checks, requiring an enrolled/managed device before a
request even reaches HomeAgent — but it requires installing a client on
every device, which is exactly the friction the household chose to avoid
by not going VPN-first for web chat; not worth it unless passkey-per-person
turns out to be insufficient in practice.

### E. Admin UI shape for Goal 4's permission matrix

New section on the existing admin dashboard (not a separate app) — a
table of household members × surfaces (Telegram / web chat / admin) with
toggles, following the same row-plus-action-button pattern already used
for the World Model and Event Rules tabs in `app/control/dashboard.html`.
Backed by a new mutation endpoint alongside the existing read-only
`/admin/users`, gated by the same `require_admin_auth` every other admin
mutation already uses. No real alternatives worth comparing here — this
is squarely "extend the existing pattern," not a new one.

### Noted, not requiring a decision here

PWA install now works without any network-topology caveat: since web
chat is reachable via its own public hostname (same as Telegram), an
installed PWA works the same whether the phone is on the home Wi-Fi, on
cellular data, or anywhere else — no VPN client required on the device at
all. This is a direct, positive consequence of the Decisions above and
closes out the original "why now" motivation (PWA + reachable outside the
LAN) cleanly. Admin has the opposite property on purpose: it stays
VPN/LAN-only, so admin access is only ever as available as the VPN
connection is — an intentional asymmetry between the two surfaces, not an
oversight.
