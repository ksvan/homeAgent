# Flight Monitor Agent Tool Design

## Purpose

Build a flight monitoring capability for HomeAgent so frequent travellers can stay on top of:

- delays and cancellations
- gate and terminal assignments
- boarding / departure / arrival timing
- aircraft and route changes
- diversions
- baggage carousel where available
- status questions like "what is happening with my flight tonight?"

The target experience is as close to Flighty as practical within HomeAgent:

- the user gives a flight and date
- HomeAgent tracks it persistently
- vendor events and/or polling keep status fresh
- significant changes trigger the agent and notify the user
- the user can ask follow-up questions reactively

This is design only. No code changes yet.

## What Makes This Different From Normal Tools

Most HomeAgent tools are called only inside a user conversation. Flight monitoring needs both:

1. **Reactive lookup tools** for questions asked in chat.
2. **Proactive event ingestion** from flight-data vendors so HomeAgent can wake up when the world changes.

So the design needs:

- a vendor abstraction
- persistent flight watch records
- webhook/event ingestion
- scheduled polling fallback
- change detection and notification policy
- a path from incoming flight events into the existing agent run loop

## Vendor Landscape

### Summary

| Vendor | Data richness | Freshness | Event support | Fit |
| --- | --- | --- | --- | --- |
| AeroDataBox | Good practical status/tracking, schedules, airport boards, airport delays, flight alerts; coverage may be incomplete | Real-time / near real-time where covered | Good: Flight Alert PUSH API with webhooks for flight number or airport subscriptions | Recommended V1 candidate because cost fits and event support exists |
| OAG Flight Info | Very strong: status, gates, terminals, baggage, codeshares, aircraft, recovery | Near real-time | Strong: Flight Info Alerts via Azure Event Hubs and HTTP push/beta | Best data/event shape, likely enterprise/commercial contract |
| Cirium / FlightStats | Strong: status, track, gates, terminals, baggage, delay, alerts | Near real-time/current | Strong: Alerts API supports push-based alerting to HTTPS endpoints | Strong candidate if access/pricing works |
| FlightAware AeroAPI | Strong tracking/status, alerts, positions, historical; gate detail may vary | Near real-time | Good: AeroAPI post alerts to target URL | Technically good, but likely too expensive for this household use case |
| Amadeus On-Demand Flight Status | Useful: times, terminal/gate, duration, delay | Pull only | No apparent push event support | Good fallback/lookup source, not enough alone for Flighty-like proactive monitoring |
| Aviationstack / AirLabs / FlightLabs | Varies | Varies | Usually weak or no first-class webhook | Consider only as low-cost fallback after testing data quality |

### AeroDataBox

AeroDataBox is the best fit for the current cost target. It is explicitly aimed
at smaller applications, individual developers, and teams that cannot justify
enterprise aviation-data pricing. Public pricing shows free/trial and low-cost
plans around 5 USD/month on API.Market/RapidAPI, with API units included per
month. AeroDataBox also has a Flight Alert PUSH API that can send webhook
notifications when subscribed flights or airport movements are updated.

Relevant capabilities:

- flight status by flight number, registration, Mode-S, or ATC callsign
- flight history and future schedules
- airport arrivals/departures / FIDS-style schedules
- flight number autocomplete
- airport details and local times
- airport delay statistics
- flight alert webhook subscriptions by flight number or airport ICAO code
- affordable plans with API-unit quotas

Important limitations:

- AeroDataBox says coverage is extensive but not worldwide.
- It positions itself as a niche, best-effort API without the same quality/SLA
  expectations as larger vendors.
- V1 access will be through RapidAPI, so the exact base URL, host header, plan
  limits, and whether the Flight Alert PUSH endpoints are exposed in the chosen
  RapidAPI subscription must be confirmed from the active subscription page.
- Gate, terminal, baggage, inbound-aircraft and "plane at gate" quality must be
  tested on the actual Nordic/Baltic routes used by the household.
- Alert delivery is credit-based in the newer alert system. Each notification
  delivery attempt consumes alert credits, so noisy airport-level subscriptions
  can cost more than flight-specific subscriptions.
- Alerts can be subscribed by flight number or airport, but the design should
  assume flight-number subscriptions for V1 to control cost and noise.

Source:

- https://aerodatabox.com/
- https://aerodatabox.com/pricing/
- https://aerodatabox.com/flight-alert-api-2026/
- https://aerodatabox.com/faq/
- https://doc.aerodatabox.com/

Judgement:

- Recommended V1 provider.
- Best match for the target budget of roughly 10 USD/month.
- The feature should launch with strong caveats around data completeness and
  graceful fallback to polling/manual status checks.
- Do not overbuild the feature until real flights on Norwegian, SAS, Finnair,
  and Air Baltic have been tested.

### OAG

OAG Flight Status Data advertises delays, cancellations, gate changes, diversions, flight state, gate/runway times, diversion/recovery tracking, aircraft type/tail number, departure/arrival gates and terminals, check-in desk, baggage carousel, and codeshares. Tracking starts 48 hours before departure and ends 24 hours after arrival. OAG says Flight Info API and Flight Info Alerts are near real-time, and alerts can be pushed via HTTP or pulled from an event hub.

Source:

- https://www.oag.com/flight-status-data
- https://www.oag.com/flight-info-alerts
- https://knowledge.oag.com/docs/flight-info-alerts-overview

Judgement:

- Best shape for a Flighty-like product.
- Might be too enterprise-heavy/costly for household use.
- If accessible, OAG should be a top candidate because event delivery and rich status fields match the goal well.

### Cirium / FlightStats

Cirium Flight Status APIs expose scheduled, estimated, and actual departure/arrival times, equipment type, delay calculations, terminal, gate, and baggage carousel. Their Flight Status by Flight documentation says current flight information is available roughly three days before departure until roughly seven days after arrival. Cirium also has Flight Tracks for active positional data. Cirium's Alerts API is push-based and monitors specific flights or flight categories.

Source:

- https://developer.flightstats.com/api-docs/flightstatus/v2
- https://developer.flightstats.com/api-docs/flightstatus/v2/flight
- https://developer.flightstats.com/api-docs/alerts/v1
- https://www.cirium.com/data/aviation-api/

Judgement:

- Strong functional fit.
- Has both status lookup and push alerting.
- Likely a serious vendor candidate if the account/pricing path is acceptable.

### FlightAware AeroAPI

FlightAware AeroAPI offers REST status/tracking and real-time alerting. Their AeroAPI support docs describe POST alerts to a target URL for specific flights, aircraft, or routes, with events such as departure, arrival, delays, cancellations, diversions, and more. AeroAPI pricing currently shows alerts available from Standard tier, with a listed Standard monthly minimum of $100/month.

Source:

- https://www.flightaware.com/commercial/aeroapi
- https://support.flightaware.com/hc/en-us/articles/33381502369175-How-Do-I-Create-A-Post-Alert-In-AeroAPI

Judgement:

- Technically a strong webhook-capable candidate.
- Strong tracking data.
- Likely outside the desired budget for this feature.
- Need a hands-on trial to verify gate/terminal richness for the airports and airlines you actually use.
- Need to confirm exact alert payload shape and whether status-change events include gate/terminal changes or only operational milestones.

### Amadeus

Amadeus On-Demand Flight Status provides real-time schedule data including updated departure/arrival times, terminal/gate information, duration, and delay status. It is query-based.

Source:

- https://developers.amadeus.com/self-service/apis-docs/guides/developer-guides/resources/flights/

Judgement:

- Good for "what is the current status?" queries.
- Not enough alone for proactive Flighty-like monitoring because event/webhook support is not the core offering.
- Could be useful as a fallback pull provider or a lower-cost initial test if push events are deferred.

## Recommended V1 Direction

Design the HomeAgent feature around a provider interface and implement the first
provider against AeroDataBox, behind a feature flag.

Current recommendation:

1. **V1 target**: AeroDataBox, because cost and webhook support match the goal.
2. **Fallback pull-only provider**: Amadeus or another low-cost source only if
   AeroDataBox coverage is poor for the user's actual routes.
3. **Future premium option**: OAG/Cirium/FlightAware only if the feature proves
   valuable enough to justify enterprise-style pricing.

Do not hard-code the product around one vendor. Vendor coverage and payload semantics differ too much.

The first implementation should be intentionally removable. If the feature turns
out to be a bad use of HomeAgent, it should be possible to disable it by config,
drop the flight-specific scheduler jobs, and leave the rest of the agent untouched.

## Product Assumptions From User Decisions

- Main users: all household members, separately scoped by user.
- Main airlines: Norwegian, SAS, sometimes Finnair and Air Baltic.
- Main regions: Nordics and Baltics.
- Common airports: Stockholm, Oslo, Helsinki, Turku, Copenhagen.
- Budget target: around 10 USD/month.
- V1 input: manual tracking is acceptable, but the tool must be callable by later
  calendar/email ingestion modules.
- Notification preference: key travel updates and all disruptions.
- Notify overnight for critical changes.
- V1 scope is single-leg flights only. Multi-leg trips are handled as separate
  independent watches per leg. A future trip aggregation concept may link them,
  but that is not V1.
- Arrival baggage/weather and aircraft/tail/inbound-aircraft information are
  useful if the provider has them, but not mandatory for V1.

## Modularity And Feature Flag

This feature must be easy to turn off and easy to carve out later.

### Feature flag

Add one top-level flag:

```env
FEATURE_FLIGHT_MONITOR=false
```

When false:

- no flight tools are registered on the agent
- flight webhook routes return 404 or disabled response
- flight scheduler jobs are not restored
- flight provider clients are not initialized
- no flight admin panels or status blocks are shown
- existing flight tables may remain in the DB, but are inert

Provider config should also be isolated. V1 will use AeroDataBox through
RapidAPI, so the adapter should send `X-RapidAPI-Key` and `X-RapidAPI-Host` on
outbound provider calls.

```env
FLIGHT_PROVIDER=aerodatabox
FLIGHT_AERODATABOX_MARKETPLACE=rapidapi
FLIGHT_AERODATABOX_RAPIDAPI_KEY=...
FLIGHT_AERODATABOX_RAPIDAPI_HOST=aerodatabox.p.rapidapi.com
FLIGHT_AERODATABOX_BASE_URL=https://aerodatabox.p.rapidapi.com
FLIGHT_AERODATABOX_ALERTS_ENABLED=true
FLIGHT_AERODATABOX_ALLOW_AIRPORT_ALERTS=false
FLIGHT_WEBHOOK_PUBLIC_BASE_URL=https://<public-host>
FLIGHT_POLL_RECENT_WEBHOOK_SUPPRESS_MINUTES=20
FLIGHT_DEFAULT_NOTIFY_POLICY=...
FLIGHT_MONITOR_ENDS_HOURS_AFTER_ARRIVAL=24
FLIGHT_SUBSCRIPTION_RETRY_LEAD_DAYS=7
FLIGHT_WATCH_FAIL_CONSECUTIVE_ERRORS=5
FLIGHT_ALERT_MIN_CREDITS=25
FLIGHT_RAW_EVENT_RETENTION_DAYS=60
FLIGHT_COMPLETED_WATCH_RETENTION_DAYS=180
```

`FLIGHT_AERODATABOX_RAPIDAPI_HOST` should remain configurable instead of
hard-coded because RapidAPI hostnames are marketplace-owned API identifiers.
The value above is the expected AeroDataBox RapidAPI host, but implementation
should use the value shown in the active RapidAPI subscription.

`FLIGHT_SUBSCRIPTION_RETRY_LEAD_DAYS` controls how many days before departure
the scheduler begins attempting to create the provider alert subscription for
watches where it could not be created at tracking time.

`FLIGHT_WATCH_FAIL_CONSECUTIVE_ERRORS` is the number of consecutive provider
errors during active monitoring before a watch is marked FAILED.

`FLIGHT_ALERT_MIN_CREDITS` is the low-balance warning threshold for AeroDataBox
flight alert credits. Balance checks should run on startup and daily while
flight alerts are enabled.

### Module boundary

Keep all domain code under one package:

```text
app/flights/
  models.py
  repository.py
  service.py
  diff.py
  notifications.py
  providers/
    base.py
    aerodatabox.py
  scheduler.py
```

Only three integration points should touch the wider app:

- `app/agent/tools/flights.py` registers tools when the feature flag is enabled.
- `app/api/webhooks.py` routes flight webhooks to `app.flights.service`.
- startup/shutdown restores flight polling jobs when the feature flag is enabled.

Avoid mixing flight logic into task pursuit, world model, calendar tools, or
generic event rules. Flight monitoring can emit `InboundEvent` objects and admin
events, but its state machine should remain inside `app/flights`.

### Carve-out rule

If removed later, the expected deletion should be limited to:

- `app/flights/`
- `app/agent/tools/flights.py`
- flight-specific migration files
- flight-specific config entries
- one or two startup/webhook registration lines

No core agent behavior should depend on flights.

## Storage

All flight tables live in `cache.db`.

Flight data is time-bounded operational state: watches expire after trips end,
snapshots are replaced on refresh, and raw events are debug history. This puts
it in the same category as `PendingAction`, `DeviceSnapshot`, and `AgentRunLog`
— not long-lived user-intent data like tasks or action policies.

```text
cache.db tables:
  flightwatch          — one row per user-requested flight to monitor
  flightstatussnapshot — latest normalized status per watch
  flightevent          — raw and normalized incoming vendor events
```

Alembic branch: `cache_db`, same pattern as the wine migration.

## Core Architecture

With `FEATURE_FLIGHT_MONITOR=false`, none of this runtime path should be active.
The package may remain installed, but the tool registry, webhook route, provider
client, and scheduler jobs should not be wired into the running app.

```text
User chat
  |
  | add flight / ask flight status
  v
Agent flight tools
  |
  v
Flight Service
  |
  +--> Flight Vendor Adapter
  |       +--> lookup flight status
  |       +--> create/delete vendor alert subscription
  |       +--> normalize vendor payloads
  |
  +--> Flight Repository
  |       +--> flight watches
  |       +--> status snapshots
  |       +--> raw vendor events
  |
  +--> Scheduler (fallback polling + deferred subscription retry)

Vendor webhook / event stream
  |
  v
/webhook/flights/{vendor}/{watch_token}
  |
  v
verify -> normalize -> dedupe -> update snapshot -> emit admin events
  |
  v
Inbound Event Bus
  |
  v
Agent run dispatched as background task (not awaited inline)
  |
  v
Telegram notification / user answer
```

## Data Model

### `FlightWatch`

Represents the user's intent to monitor a flight. Segment details (carrier,
aircraft, codeshares) are stored directly here — no separate segment table for
V1, since each watch covers a single leg.

Fields:

```python
id: str
household_id: str
user_id: str
channel_user_id: str

label: str | None                       # "Monday to Stockholm"
carrier_code: str                       # IATA or ICAO, e.g. SK / SAS
flight_number: str                      # e.g. 1461
scheduled_departure_date: date
origin: str | None                      # IATA/ICAO if known
destination: str | None

# Segment details from first resolution
operating_carrier_code: str | None
marketing_carrier_code: str | None
codeshares_json: str                    # JSON list
aircraft_type: str | None
tail_number: str | None

status: str                             # ACTIVE | COMPLETED | CANCELLED | FAILED
status_reason: str | None               # machine-readable reason for non-ACTIVE status
monitoring_starts_at: datetime | None
monitoring_ends_at: datetime | None     # default: scheduled_arrival + FLIGHT_MONITOR_ENDS_HOURS_AFTER_ARRIVAL

provider: str
provider_flight_id: str | None
provider_alert_id: str | None           # None until alert subscription is created
provider_subscription_kind: str | None  # "flight_number" | None
webhook_token_hash: str | None          # hash of high-entropy random webhook token

consecutive_provider_errors: int        # reset on success; FAILED when reaches threshold
notify_policy_json: str
created_at: datetime
updated_at: datetime
completed_at: datetime | None
```

### `FlightStatusSnapshot`

Latest normalized operational status.

Fields:

```python
id: str
watch_id: str
provider: str
provider_updated_at: datetime | None
fetched_at: datetime

state: str
# scheduled, estimated, actual times
scheduled_out: datetime | None
estimated_out: datetime | None
actual_out: datetime | None
scheduled_off: datetime | None
estimated_off: datetime | None
actual_off: datetime | None
scheduled_on: datetime | None
estimated_on: datetime | None
actual_on: datetime | None
scheduled_in: datetime | None
estimated_in: datetime | None
actual_in: datetime | None

departure_terminal: str | None
departure_gate: str | None
arrival_terminal: str | None
arrival_gate: str | None
baggage_claim: str | None

delay_minutes: int | None
cancelled: bool
diverted: bool
diversion_airport: str | None
raw_json: str
```

### `FlightEvent`

Stores raw and normalized incoming vendor events.

Fields:

```python
id: str
watch_id: str | None
provider: str
provider_event_id: str | None
event_hash: str
event_type: str
severity: str
received_at: datetime
provider_timestamp: datetime | None
raw_json: str
normalized_json: str
processed: bool
```

## Normalized Flight States

Use one internal vocabulary even if vendors differ:

```text
SCHEDULED
CHECK_IN_OPEN
BOARDING
OUT_GATE
IN_AIR
LANDED
IN_GATE
CANCELLED
DIVERTED
UNKNOWN
```

Some vendors will not provide all states. The adapter should map the best available signal and keep raw details in `raw_json`.

## FlightWatch State Machine

### States

| Status | Meaning |
| --- | --- |
| `ACTIVE` | Watch is running — polling and/or alert subscription are live |
| `COMPLETED` | Flight finished normally — monitoring ended |
| `CANCELLED` | Watch stopped — either by user request or because the airline cancelled the flight |
| `FAILED` | Watch stopped due to an unrecoverable error — user should use another app |

### Transitions

#### ACTIVE → COMPLETED

Triggered by either of:

1. Webhook or poll receives a terminal flight state (`IN_GATE` or equivalent) and
   all post-arrival monitoring window logic is satisfied.
2. Scheduler reaches `monitoring_ends_at` — the fallback cleanup for cases where
   the terminal state signal was never received (data quality, connectivity gap).

User is **not** notified. Monitoring ending normally is the expected outcome.

#### ACTIVE → CANCELLED

Triggered by either of:

1. User calls `cancel_flight_watch` — explicit user action.
2. Incoming flight event carries state `CANCELLED` — the airline cancelled the
   flight.

In both cases: notify the user. For airline cancellations the agent produces a
short practical update ("Your flight SK1461 was cancelled. HomeAgent has stopped
monitoring it. Check the airline app for rebooking options."). For user-initiated
cancellation, a brief confirmation is sufficient.

#### ACTIVE → FAILED

Triggered by either of:

1. Provider cannot resolve the flight at `track_flight` time (unrecoverable
   resolution failure — ambiguous result that the user did not clarify).
2. `consecutive_provider_errors` reaches `FLIGHT_WATCH_FAIL_CONSECUTIVE_ERRORS`
   (default 5) during active monitoring without any successful refresh in between.
   The counter resets on any successful poll or webhook receipt.

Failed watches are **not** auto-retried. The user must re-add the watch.

Notify the user on FAILED: "HomeAgent lost contact with the flight data provider
for SK1461 and has stopped monitoring. Please use the airline app or another
flight tracker." The agent should include any last-known status in the message
if available.

### On any terminal transition (COMPLETED, CANCELLED, FAILED)

The service must:

1. Attempt to delete the vendor alert subscription if `provider_alert_id` is set.
2. Remove the watch's polling jobs from the scheduler.
3. Emit the appropriate admin event.

Deletion failure for the vendor subscription should be logged but not block the
state transition — the watch is terminal regardless.

## Alert Subscription Timing

AeroDataBox and similar vendors have a lead-time window for alert subscriptions.
A user may add a watch for a flight weeks or months in advance when the provider
cannot yet create the subscription.

Handling:

1. `track_flight` attempts to create the subscription immediately.
2. If the provider rejects it because the flight is too far out, the watch is
   saved with `provider_alert_id = None` and a fallback polling schedule.
   The user is informed: "I'm tracking this flight. Alert subscription will be
   activated closer to departure — polling is active in the meantime."
3. A daily scheduler job checks all `ACTIVE` watches with `provider_alert_id = None`
   and `scheduled_departure_date` within `FLIGHT_SUBSCRIPTION_RETRY_LEAD_DAYS`
   (default 7). It attempts subscription creation for each and updates the watch
   if successful. Emits an admin event on success or persistent failure.
4. If the subscription creation is still failing within 24 hours of departure,
   the service emits a `flight.provider_alert_failed` event and continues on
   polling only — it does **not** fail the watch.

## Vendor Quota Handling

The provider enforces API unit and alert credit quotas. HomeAgent does not
maintain its own internal counter. When the vendor returns a quota error
(typically HTTP 429 or a provider-specific error code):

- Log the error.
- Emit a `flight.provider_quota_exceeded` admin event.
- If it occurred during a user-initiated query, return a message: "The flight
  data provider is temporarily unavailable (quota limit). Last known status: …"
- If it occurred during a background poll or subscription attempt, skip this
  cycle, back off, and retry on the next scheduled interval.

Do not fail the watch on quota errors alone — these are expected to be transient.
Only increment `consecutive_provider_errors` for genuine connectivity or
resolution failures, not quota responses (which are recoverable and provider-
enforced).

## Alert Credit Monitoring

AeroDataBox credit-based flight alert subscriptions pause automatically when the
account's alert credit balance reaches zero. Because this can silently stop push
notifications for all watches, HomeAgent should monitor the balance separately
from normal API quota errors.

Behavior:

1. On startup, if `FEATURE_FLIGHT_MONITOR=true` and
   `FLIGHT_AERODATABOX_ALERTS_ENABLED=true`, call the free alert balance endpoint.
2. Run a daily balance check while alerts are enabled.
3. Parse remaining balance from webhook payloads if AeroDataBox includes it.
4. Emit `flight.alert_credit_low` when balance is at or below
   `FLIGHT_ALERT_MIN_CREDITS`.
5. Emit `flight.alert_credit_empty` when balance reaches zero.
6. Notify admin/user only when the threshold state changes, not on every check.
7. Do not auto-refill credits in V1.

If the balance is zero, keep watches active but treat alert delivery as paused:
continue conservative polling, show stale/provider warnings where relevant, and
make the admin stream clearly show that push alerts are credit-blocked.

## Agent Tool Surface

### `track_flight`

Use when the user wants HomeAgent to monitor a flight.

Inputs:

```python
carrier_code: str
flight_number: str
departure_date: str
origin: str | None = None
destination: str | None = None
label: str | None = None
notify_policy: dict | None = None
```

Behavior:

1. Resolve flight with provider API.
2. Ask clarification if multiple candidates match.
3. Persist `FlightWatch`.
4. Generate a high-entropy webhook token, store only its hash, and build the
   public webhook URL for this watch.
5. Attempt provider alert subscription. If deferred, inform the user.
6. Schedule polling fallback.
7. Return a concise confirmation with currently known status.

For AeroDataBox V1, prefer a flight-number alert subscription over an airport
subscription. Airport subscriptions are likely too noisy for a household use
case and can burn alert credits quickly.

### `get_flight_status`

Use for questions like "how is my flight looking?"

Inputs:

```python
flight_watch_id: str | None = None
carrier_code: str | None = None
flight_number: str | None = None
departure_date: str | None = None
```

Behavior:

- If a watch exists, refresh it and return current snapshot.
- If no watch exists but enough identifiers are provided, perform an ad hoc lookup.
- If the user has one upcoming flight, use that by default.

### `list_tracked_flights`

Returns active and recent flight watches for the user.

### `cancel_flight_watch`

Cancels monitoring and removes provider alert subscription where supported.

### Later: `update_flight_notify_policy`

Allows user-specific thresholds:

- notify on any delay
- only delay over N minutes
- notify on gate/terminal changes
- notify when boarding starts
- quiet hours / "only urgent overnight"

### Later source-ingestion tool boundary

Calendar/email ingestion should be a separate feature, but the flight module
should expose a narrow service method that other modules can call:

```python
register_flight_watch_from_source(
    user_id: str,
    source: str,                 # "manual" | "calendar" | "email" | ...
    carrier_code: str,
    flight_number: str,
    departure_date: date,
    origin: str | None = None,
    destination: str | None = None,
    source_ref: str | None = None,
)
```

This keeps parsing messy calendar/email content outside the flight monitor while
still letting those modules create watches later.

## Incoming Event Flow

### Webhook endpoint

Each flight watch gets its own unique webhook URL using a high-entropy random
token generated when the watch is created. Store only a hash of the token on
`FlightWatch`. Do not use `watch_id` as the secret; watch IDs are identifiers
and may appear in admin views, logs, tool responses, or support debugging.

```text
POST /webhook/flights/{vendor}/{webhook_token}
```

The service hashes the path token, resolves the corresponding watch, and
validates that the vendor path segment matches the watch's `provider` field.

Security:

- Use a high-entropy random path token — each URL is unique to one watch.
- Store only the token hash in the database.
- Do not log the raw token or include it in admin events.
- Validate content type and body size.
- Verify vendor identity from the path matches the watch's stored provider.
- If the vendor supports additional request signatures or headers, validate those too.
- Store raw payload for debugging, but do not log at INFO level.
- Deduplicate by vendor event ID or stable payload hash.

Processing:

1. Verify request (token hash resolves to a valid watch, vendor matches).
2. Normalize vendor payload to `FlightEvent`.
3. Fetch current status if event payload is incomplete.
4. Diff against previous `FlightStatusSnapshot`.
5. Persist new snapshot.
6. Emit admin events.
7. Dispatch a flight notification background task for significant changes — **do not await the agent run inline**. The webhook handler must return quickly; a slow LLM response must not block the vendor's delivery window or trigger retries.

### Agent run dispatch

When a change is significant enough to wake the agent:

- Dispatch directly from `app.flights.notifications.dispatch_flight_update(...)`.
- The flight service already knows `watch.user_id`, `watch.household_id`, and
  `watch.channel_user_id`, so it should not depend on user-created `EventRule`
  records or the generic event-rule matcher.
- The agent run acquires the per-user lock, same as task resumes.
- The webhook handler returns HTTP 200 before the agent run completes.

Flight events may still emit an `InboundEvent` later if useful for generic
control-loop visibility, but V1 notification delivery should not depend on the
existing event dispatcher. The current dispatcher only wakes the agent when an
`EventRule` matches; flight watches are already explicit user intent and should
trigger from their own service boundary.

### Optional InboundEvent shape

```python
InboundEvent(
    source="flight",
    event_type="flight_status_changed",
    household_id=...,
    entity_id=flight_watch_id,
    payload={
        "watch_id": "...",
        "flight": "SK1461",
        "date": "2026-05-04",
        "change_type": "gate_changed",
        "severity": "info",
        "old": {"departure_gate": "A12"},
        "new": {"departure_gate": "A18"},
        "summary": "Departure gate changed from A12 to A18",
    },
)
```

This is optional for V1 and should be used only for generic control-plane
visibility. User notification must use direct flight dispatch, not event-rule
matching.

## Agent Triggering Policy

Do not wake the LLM for every vendor event. First do deterministic filtering and diffing.

Wake the agent for:

- cancellation
- diversion
- delay above user threshold
- gate or terminal change
- boarding-related status
- departure/arrival time changes above threshold
- flight not found / provider ambiguity requiring user action
- provider events that imply a travel decision
- watch moved to FAILED (notify user to use another app)

Do not wake the agent for:

- duplicate events
- tiny ETA movement below threshold
- metadata-only changes with no user impact
- raw position updates unless user opted in

When waking the agent, use the normal `agent_run(...)` path with the per-user lock. The prompt should include:

- flight identity
- previous and current normalized status
- what changed
- user notification policy
- instruction to produce a short practical update

Example:

```text
## Flight Monitor Event
- flight: SK1461 OSL -> CPH
- date: 2026-05-04
- change: departure gate changed
- old gate: A12
- new gate: A18
- scheduled departure: 18:05 Europe/Oslo
- current delay: 10 minutes

Tell the user what changed and what they should do, briefly.
```

## Polling Fallback

Even with webhooks, use polling as a safety net. Because the target budget is
around 10 USD/month and AeroDataBox plans are API-unit / alert-credit based,
polling should be conservative by default.

Suggested schedule:

| Time window | Poll interval |
| --- | --- |
| More than 7 days before departure | no polling, only stored watch |
| 7 days to 48 hours before | no regular polling unless user asks |
| 48 to 12 hours before | every 6 hours |
| 12 to 4 hours before | every 2 hours |
| 4 to 1 hours before | every 30 minutes |
| 1 hour before to arrival | every 15 minutes unless alert events are flowing |
| After arrival until monitoring end | every 30-60 minutes until IN_GATE/complete |

If provider event support is reliable, lower the polling frequency.

Cost guardrails:

- skip the next poll when a recent webhook already refreshed the same watch
  within `FLIGHT_POLL_RECENT_WEBHOOK_SUPPRESS_MINUTES` (default 20 minutes)
- stop polling when the flight reaches a terminal state
- never create airport-level subscriptions in V1 unless explicitly enabled
- vendor enforces quota — HomeAgent handles quota errors gracefully (see Vendor
  Quota Handling section) rather than tracking its own unit counter

Monitoring end:

- default: `monitoring_ends_at = scheduled_arrival + FLIGHT_MONITOR_ENDS_HOURS_AFTER_ARRIVAL` (default 24)
- earlier if state is `IN_GATE` and no further post-arrival tracking is needed

## Notification Policy

Default policy:

```json
{
  "delay_threshold_minutes": 15,
  "notify_gate_changes": true,
  "notify_terminal_changes": true,
  "notify_cancellations": true,
  "notify_diversions": true,
  "notify_boarding": true,
  "notify_aircraft_changes": false,
  "notify_inbound_aircraft_arrived": true,
  "notify_minor_time_changes": false,
  "quiet_hours_mode": "urgent_only"
}
```

Severity:

- `critical`: cancelled, diverted, major delay, missed-connection risk, watch FAILED
- `warning`: delay above threshold, terminal change, significant gate/time change
- `info`: boarding, gate assigned, baggage carousel assigned, inbound aircraft arrived
- `debug`: duplicate/no-impact event

## Admin and Observability Events

Emit enough events for the admin stream:

| Event | Meaning |
| --- | --- |
| `flight.watch_created` | User started monitoring a flight |
| `flight.watch_completed` | Flight finished and monitoring ended |
| `flight.watch_cancelled` | User stopped monitoring or airline cancelled the flight |
| `flight.watch_failed` | Watch moved to FAILED — user notified to use another app |
| `flight.feature_disabled` | Flight tool/webhook/scheduler path was skipped by config |
| `flight.provider_alert_created` | Vendor alert subscription created |
| `flight.provider_alert_deferred` | Subscription creation deferred — flight too far out |
| `flight.provider_alert_deleted` | Vendor alert subscription deleted |
| `flight.provider_alert_failed` | Vendor alert creation failed within alert window |
| `flight.webhook_received` | Vendor webhook accepted |
| `flight.webhook_rejected` | Vendor webhook failed validation |
| `flight.event_duplicate` | Duplicate vendor event ignored |
| `flight.status_refreshed` | Poll or lookup refreshed current status |
| `flight.status_changed` | Normalized status diff produced meaningful change |
| `flight.poll_skipped` | Poll skipped because webhook data is fresh |
| `flight.provider_quota_exceeded` | Vendor returned a quota error |
| `flight.alert_credit_low` | AeroDataBox alert credit balance is below configured threshold |
| `flight.alert_credit_empty` | AeroDataBox alert credit balance reached zero; push alerts are paused |
| `flight.provider_error` | Vendor API call failed (increments consecutive error counter) |
| `flight.notify_suppressed` | Change did not pass user notification policy |
| `flight.agent_triggered` | Change dispatched an agent run |
| `flight.retention_cleanup_completed` | Old raw events / expired watch data were pruned |

Admin views later:

- active tracked flights
- latest status
- last vendor sync
- provider alert ID / subscription status
- raw event count
- last notification sent
- provider health

## Vendor Adapter Interface

```python
class FlightProvider:
    name: str

    async def resolve_flight(query: FlightQuery) -> list[ResolvedFlight]:
        ...

    async def get_status(provider_flight_id: str | None, query: FlightQuery) -> FlightStatus:
        ...

    async def create_alert(watch: FlightWatch, webhook_url: str) -> ProviderAlert:
        ...

    async def delete_alert(provider_alert_id: str) -> None:
        ...

    async def get_alert_credit_balance() -> AlertCreditBalance:
        ...

    def verify_webhook(headers: dict, body: bytes, webhook_token: str) -> bool:
        ...

    def normalize_webhook(body: bytes) -> FlightEvent:
        ...
```

Provider capability flags:

```python
supports_webhooks: bool
supports_gate_changes: bool
supports_terminal_changes: bool
supports_baggage: bool
supports_track_positions: bool
supports_codeshares: bool
alert_lead_time_days: int | None     # None means no known restriction
```

This keeps the rest of HomeAgent independent from vendor-specific semantics.

## Security

- Flight data is personal travel data. Treat it as private household/user data.
- Each webhook URL is unique to a watch and uses a high-entropy random token.
  A leaked URL cannot be used to inject events for other watches.
- Do not use `watch_id` as a webhook secret. Store only `webhook_token_hash` and
  compare hashes server-side.
- Vendor API keys must live in `.env`, never prompt context or logs.
- RapidAPI credentials must be treated as provider secrets. Do not include
  `X-RapidAPI-Key` in admin event payloads, raw event logs, or agent context.
- Log flight identifiers and event types, but avoid logging full raw payloads at info level.
- Restrict admin views behind existing admin auth.
- Cloudflare should only expose the needed webhook route, not admin endpoints.
- Use idempotency to prevent replay storms.

## Reliability

- Persist every `FlightWatch` and latest snapshot.
- Restore scheduled polling jobs on startup.
- Retry deferred subscription creation via daily scheduler job.
- Check AeroDataBox alert credit balance on startup and daily while alerts are enabled.
- Recreate polling jobs for active watches on startup if missing.
- Use per-user agent locks when notifying, same as task resumes and event rules.
- If vendor API fails, retain last known snapshot and disclose staleness.
- If webhook payload is incomplete, fetch details from the provider before notifying.
- If provider alert creation fails within the alert window, fall back to polling
  and emit an admin event — do not fail the watch.
- If the vendor returns a quota error, back off and inform the user if they asked
  directly; do not fail the watch.

## Retention And Cleanup

Flight data is personal travel data and should not accumulate indefinitely.

Default retention:

- raw `FlightEvent` rows: delete after `FLIGHT_RAW_EVENT_RETENTION_DAYS`
  (default 60)
- completed/cancelled/failed `FlightWatch` rows and snapshots: delete after
  `FLIGHT_COMPLETED_WATCH_RETENTION_DAYS` (default 180)
- active watches are never deleted by retention cleanup

Cleanup should run from the existing cleanup scheduler path and emit
`flight.retention_cleanup_completed` with counts for removed events, snapshots,
and watches. Raw payloads should remain available long enough to debug vendor
integration issues, but not become long-term travel history.

## Where This Fits in Existing Architecture

Likely files/modules later:

```text
app/flights/models.py
app/flights/repository.py
app/flights/service.py
app/flights/diff.py
app/flights/notifications.py
app/flights/providers/base.py
app/flights/providers/aerodatabox.py
app/flights/scheduler.py
app/agent/tools/flights.py
app/api/webhooks.py
```

This should reuse:

- FastAPI webhook pattern from Telegram/Homey
- `agent_run(...)` dispatched directly from `app.flights.notifications` as a
  background task (not awaited in webhook handler)
- per-user run locks
- admin SSE event stream
- APScheduler restore pattern
- `cache.db` Alembic branch for migrations

## Phased Implementation

### Phase 0: AeroDataBox vendor spike

Before coding the HomeAgent feature, test a few real flights with AeroDataBox
through the actual RapidAPI subscription.

Verify:

- exact `X-RapidAPI-Host` and base URL from the subscription
- whether Flight Alert PUSH subscription endpoints are available through
  RapidAPI, not only API.Market/direct documentation
- can we create a webhook alert for one specific future flight?
- what exact payload arrives?
- do gate/terminal changes produce alerts?
- does the status lookup include gate, terminal, baggage, aircraft, delay?
- how soon before departure does data become available (alert lead time)?
- how well does it cover the airlines/airports you use?
- how API-unit and alert-credit usage behaves for a normal travel week
- whether airport-level subscriptions are too noisy

### Phase 1: Read-only status lookup

- Add `FEATURE_FLIGHT_MONITOR=false` and keep it disabled by default.
- Implement provider adapter for AeroDataBox.
- Add `get_flight_status`.
- Add `track_flight` persistence without vendor alerts if necessary.
- Add polling refresh.
- Let the agent answer user questions about tracked flights.

### Phase 2: Webhook/event monitoring

- Add `/webhook/flights/{vendor}/{webhook_token}`.
- Add AeroDataBox flight-number alert subscription creation/deletion.
- Handle deferred subscriptions and retry scheduler job.
- Add alert credit balance checks.
- Normalize incoming events.
- Diff snapshots.
- Emit admin events.
- Dispatch agent run directly from `app.flights.notifications` as a background
  task for significant changes.

### Phase 3: Flighty-like polish

- Better notification policy.
- Boarding reminders.
- Inbound aircraft tracking if provider supports it.
- Airport weather / delay index context.
- Admin dashboard flight tab.

### Phase 4: Trip source ingestion

Optional later:

- parse flights from calendar events
- parse airline confirmation emails
- integrate with travel booking systems

For V1, manual `track_flight` is simpler and safer.

### Potential future phases — backlog

- More data queries supported for the agent if user asks, e.g. plane data, historic delays
- Security control queue lengths where available
- Multi-leg trip aggregation (link independent single-leg watches into a trip view)

## Answered Questions And Remaining Decisions

| Topic | Decision |
| --- | --- |
| Users | All household members, separately scoped |
| Common airlines | Norwegian and SAS; sometimes Finnair and Air Baltic |
| Common region | Nordics and Baltics |
| Common airports | Stockholm, Oslo, Helsinki, Turku, Copenhagen |
| Vendor budget | Around 10 USD/month |
| V1 input mode | Manual tracking is acceptable |
| Later input sources | Calendar events or booking emails, implemented as separate ingestion modules |
| Ingestion boundary | Flight module should accept new watches from calendar/email modules through a narrow service API |
| Segments | V1: independent single-leg watches only. Multi-leg = multiple watches. |
| Notification style | Key travel updates, gate assignment, all disruptions, useful arrival estimates |
| Overnight notifications | Yes, for critical changes |
| Arrival details | Baggage, arrival weather, etc. are useful if available |
| Aircraft details | Tail number/inbound aircraft is nice-to-have; "plane arrived / at gate" is useful |
| Database | `cache.db` — all flight tables are time-bounded operational state |
| FlightSegment table | Removed — segment fields folded into `FlightWatch` for V1 |
| Webhook security | Watch-scoped high-entropy random token (`/webhook/flights/{vendor}/{webhook_token}`); store only token hash |
| Deferred subscriptions | Attempt at track_flight time; retry via daily scheduler job within `FLIGHT_SUBSCRIPTION_RETRY_LEAD_DAYS` of departure; user informed if deferred |
| Polling budget tracking | Not tracked by HomeAgent — vendor enforces quota; handle quota errors gracefully |
| Alert credit monitoring | Check on startup/daily and from webhook payloads; warn on low/empty balance; no auto-refill in V1 |
| Watch COMPLETED trigger | Terminal flight state from API (primary) + `monitoring_ends_at` scheduler fallback |
| Watch CANCELLED trigger | User action OR airline cancellation from provider event |
| Watch FAILED trigger | Unrecoverable resolution failure at creation OR `FLIGHT_WATCH_FAIL_CONSECUTIVE_ERRORS` consecutive errors during monitoring |
| Terminal state cleanup | System deletes vendor alert subscription and polling jobs on any terminal transition |
| Notification on COMPLETED | None — normal outcome |
| Notification on CANCELLED | Yes — user notified, agent produces short practical message |
| Notification on FAILED | Yes — user notified to use another app; last known status included if available |
| Agent triggering | Background dispatch directly from `app.flights.notifications`; webhook handler never awaits the agent run |
| Retention | Raw events pruned after `FLIGHT_RAW_EVENT_RETENTION_DAYS`; terminal watches after `FLIGHT_COMPLETED_WATCH_RETENTION_DAYS` |

Remaining decisions before implementation:

1. Which RapidAPI AeroDataBox plan should be used, and what exact API-unit and
   alert-credit quotas apply?
2. Confirm airport-level alert subscriptions stay disabled for V1. Recommendation:
   yes, use flight-number alerts only.
3. Confirm the default disruption threshold. Recommendation: delay over 15
   minutes, cancellation/diversion always critical.
4. Decide the user-facing behavior when the provider cannot find a flight:
   ask for origin/destination clarification first, then offer manual retry later.
5. Confirm that RapidAPI exposes the Flight Alert PUSH endpoints for the selected
   subscription. If not, V1 should start as pull-only lookup with conservative
   polling or switch marketplace. This is the Phase 0 spike gate.
6. Confirm the exact AeroDataBox alert subscription lead time from Phase 0 testing
   (to tune `FLIGHT_SUBSCRIPTION_RETRY_LEAD_DAYS`).
7. Confirm the initial low-credit threshold for `FLIGHT_ALERT_MIN_CREDITS`.

## Claude Review Checklist

Before implementation, ask Claude to challenge these parts specifically:

- Is the `app/flights/` boundary clean enough to remove the feature later?
- Are all runtime paths properly gated by `FEATURE_FLIGHT_MONITOR`?
- Does the RapidAPI config avoid hard-coding marketplace-specific values?
- Is the watch-scoped random webhook token model sufficient if AeroDataBox cannot sign requests?
- Are polling defaults conservative enough for the target monthly cost?
- Is alert credit monitoring enough without auto-refill in V1?
- Are enough admin/SSE events emitted to diagnose provider, webhook, polling, and
  notification behavior?
- Is calendar/email ingestion clearly out of scope while still having a clean
  future registration API?
- Is the deferred subscription retry simple enough to not add meaningful complexity?

## Current Judgement

The right design is a persistent flight-watch service with a vendor adapter, webhook ingestion, polling fallback, normalized status snapshots, and deterministic change filtering before the LLM is triggered.

For vendor choice, start with AeroDataBox because it is the only identified
provider that currently fits the target budget and still offers webhook-style
flight alerts. Keep the feature modular, off by default, and easy to remove.
If AeroDataBox coverage is poor for Norwegian/SAS/Finnair/Air Baltic in the
Nordics/Baltics, either fall back to a pull-only lookup feature or defer the
proactive monitoring feature until a better-priced provider is available.
