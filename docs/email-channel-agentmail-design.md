# Email Channel Design — AgentMail

Status: implemented, feature-flagged runtime
Last code check: 2026-06-26
Runtime entry points: `app/email/`, `app/api/webhooks.py`,
`app/agent/tools/email.py`

## Purpose

Add email intake for HomeAgent using AgentMail.

Email should work like a separate, untrusted intake channel alongside Telegram:

- users can send or forward email to the agent
- HomeAgent resolves the sender to a `User.id`
- HomeAgent extracts the user's likely instruction and relevant email details
- HomeAgent asks for confirmation in Telegram before acting
- workflows such as flight tracking can be proposed from forwarded booking
  confirmations, then executed only after Telegram confirmation

Telegram remains the trusted interactive/control channel. Email is an additional
intake channel for cases where email is the natural source format, especially
travel bookings, receipts, documents, and longer written instructions.

The important trust boundary is:

```text
Email can create intake candidates.
Telegram confirms actions.
```

V1 should not treat inbound email as an authenticated command channel even when
the sender maps to a known user.

## Target Experience

Example:

```text
From: kristian@example.com
To: agent@...
Subject: Fwd: Your trip to Stockholm

Track these flights

---------- Forwarded message ----------
From: airline@example.com
...
SK1461 OSL -> CPH ...
...
```

HomeAgent should:

1. Receive the email via AgentMail webhook.
2. Resolve `kristian@example.com` to Kristian's existing `User.id` through
   `ChannelMapping(channel="email")`.
3. Extract the user's top instruction: `Track these flights`.
4. Build a compact intake summary with candidate flight details.
5. Send Kristian a Telegram confirmation prompt.
6. After Telegram confirmation, run the flight-tracking workflow.
7. Optionally reply by email with a short acknowledgement/result.

## Vendor Fit: AgentMail

AgentMail provides a full inbox for agents, including sending, receiving,
replying, forwarding, and real-time inbound notifications.

Relevant AgentMail capabilities:

- real email inbox/address for the agent
- receive email from the internet
- send and reply through API
- webhooks for `message.received`
- WebSockets as a development/no-public-URL alternative
- full message fetch when webhook payload omits body due to payload size
- attachment metadata in webhook payload, attachment content via API
- Svix webhook signatures

Sources:

- <https://docs.agentmail.to/webhooks-overview>
- <https://docs.agentmail.to/webhook-verification>
- <https://www.agentmail.to/docs/knowledge-base/handling-inbound-emails>
- <https://www.agentmail.to/docs/knowledge-base/inbox-capabilities>

## Design Principles

1. Email is an intake channel, not a memory source by default.
2. Email is untrusted intake, not a direct command channel.
3. Always resolve sender identity before creating user-scoped intake.
4. Unknown senders must not get access to the agent or personal memory.
5. Webhook handler must return quickly and process in the background.
6. Do not put raw email fluff into long-term conversation history.
7. Preserve enough minimized metadata and derived summaries for debugging and
   tool follow-up.
8. Prefer deterministic or cheap structured extraction before using the LLM.
9. Let the agent force a bounded inbox check when the user asks from Telegram.
10. Treat forwarded email content as untrusted data, not instructions.
11. Make webhook processing durable before acknowledging provider delivery.
12. Always require Telegram confirmation before meaningful action.
13. Throttle intake before any expensive LLM run or user notification.

## Configuration

Add `.env` settings:

```env
FEATURE_EMAIL_CHANNEL=false

AGENTMAIL_API_KEY=...
AGENTMAIL_INBOX_ID=...
AGENTMAIL_ADDRESS=agent@example.agentmail.to
AGENTMAIL_WEBHOOK_ID=...
AGENTMAIL_WEBHOOK_SECRET=whsec_...
AGENTMAIL_WEBHOOK_PUBLIC_URL=https://<cloudflare-public-host>/webhook/agentmail

EMAIL_CHANNEL_USE_CLOUDFLARE=true
EMAIL_CHANNEL_CONFIRM_VIA_TELEGRAM=true

EMAIL_CHANNEL_REQUIRE_MAPPED_SENDER=true
EMAIL_CHANNEL_SAVE_HISTORY=false
EMAIL_CHANNEL_MAX_AGENT_CHARS=12000
EMAIL_CHANNEL_MAX_RAW_BODY_BYTES=1048576
EMAIL_CHANNEL_LOOKBACK_HOURS=24
EMAIL_CHANNEL_FORCE_CHECK_LIMIT=10
EMAIL_CHANNEL_RETENTION_DAYS=90
EMAIL_CHANNEL_RAW_DEBUG_RETENTION_DAYS=7
EMAIL_CHANNEL_STORE_RAW_BODY=false
EMAIL_CHANNEL_REPLY_TO_UNMAPPED=false
EMAIL_CHANNEL_ALLOW_REPLY_TO=false
EMAIL_CHANNEL_REQUIRE_AUTH_PASS=true
EMAIL_CHANNEL_SVIX_TOLERANCE_SECONDS=300
EMAIL_CHANNEL_RATE_LIMIT_PER_SENDER_PER_HOUR=20
EMAIL_CHANNEL_RATE_LIMIT_PER_USER_PER_HOUR=30
EMAIL_CHANNEL_GLOBAL_INTAKE_PER_MINUTE=30
EMAIL_CHANNEL_CONFIRMATION_BURST_WINDOW_MINUTES=10
EMAIL_CHANNEL_CONFIRMATION_BURST_THRESHOLD=3
EMAIL_CHANNEL_MAX_PROCESSING_ATTEMPTS=3
EMAIL_CHANNEL_RETRY_BASE_SECONDS=60
EMAIL_CHANNEL_DEAD_LETTER_AFTER_HOURS=24
```

Notes:

- `AGENTMAIL_API_KEY` is the AgentMail API token.
- `AGENTMAIL_WEBHOOK_SECRET` is the Svix signing secret for the webhook endpoint.
- `EMAIL_CHANNEL_SAVE_HISTORY=false` is the recommended default because email
  often contains repeated quoted content, signatures, disclaimers, marketing
  text, and forwarded thread bodies.
- `EMAIL_CHANNEL_REQUIRE_AUTH_PASS=true` is mandatory for production webhook
  intake. Mapped senders do not create user-scoped intake or Telegram prompts
  unless provider authentication passes.
- If AgentMail cannot expose a trustworthy SPF/DKIM/DMARC or equivalent
  verdict, webhook intake must degrade to metadata-only admin events. The user
  can still initiate a pull from Telegram with `check_email_now()`.
- `EMAIL_CHANNEL_STORE_RAW_BODY=false` means raw email bodies are not stored by
  default. Store minimized/redacted provider metadata and compact derived
  summaries instead.
- `EMAIL_CHANNEL_RAW_DEBUG_RETENTION_DAYS=7` is an upper bound for temporary raw
  debug payloads when explicitly enabled or needed for failed processing.
- `EMAIL_CHANNEL_SVIX_TOLERANCE_SECONDS=300` is the maximum accepted webhook
  timestamp skew for Svix signature verification.
- `EMAIL_CHANNEL_ALLOW_REPLY_TO=false` means `Reply-To` is not used for identity
  or default replies. Replies go to the mapped outer `From` sender.
- `EMAIL_CHANNEL_CONFIRM_VIA_TELEGRAM=true` is mandatory for V1. Email can
  propose work; Telegram confirms work.
- `EMAIL_CHANNEL_USE_CLOUDFLARE=true` means the public AgentMail webhook should
  terminate at the same Cloudflare ingress pattern used for other public
  webhooks before traffic reaches HomeAgent.

## Architecture

```text
AgentMail message.received webhook
  |
  v
Cloudflare public webhook ingress
  |
  | WAF / request limits / public endpoint shielding
  v
/webhook/agentmail
  |
  | verify Svix signature using raw body
  | validate body size, event type, inbox id
  | persist/dedupe EmailMessage row before ack
  v
Email Intake Service
  |
  +--> Email Repository (cache.db)
  +--> AgentMail Client
  +--> Email Preprocessor
  +--> Intake Classifier
  +--> Telegram Confirmation Prompt
  |
  v
Confirmed Telegram action
  |
  v
agent_run(...) / workflow tools
```

The webhook should not do a long agent run inline. It should persist a durable
intake row first, acknowledge the provider only after that write succeeds, and
let a background worker claim and classify pending rows. If the write fails,
return non-2xx so the provider can retry.

Email classification should produce a proposed action, not execute it. Telegram
confirmation is the gate before `agent_run(...)`, flight-watch creation, memory
writes, task creation, or other meaningful side effects.

## Cloudflare Ingress

Use the same Cloudflare public webhook pattern as the rest of the deployment
where practical:

```text
AgentMail -> Cloudflare URL -> HomeAgent /webhook/agentmail
```

Cloudflare responsibilities:

- provide the stable public webhook URL
- hide the direct HomeAgent endpoint from the internet
- enforce coarse request and body-size limits before traffic reaches HomeAgent
- apply WAF/bot filtering where available
- rate-limit obvious bursts by path, source IP, and request characteristics
- provide edge logs for debugging delivery spikes or abuse

HomeAgent responsibilities remain mandatory:

- verify Svix signatures on the raw body
- enforce Svix timestamp tolerance and persistent `svix-id` dedupe
- validate configured AgentMail inbox id
- dedupe provider event/message ids
- persist before provider acknowledgement
- apply sender/user/global intake throttles
- avoid agent runs until Telegram confirmation

Cloudflare is defense-in-depth, not the trust anchor. The Svix signature and
server-side identity mapping still decide whether an event is accepted.

## Module Boundary

Keep the feature isolated:

```text
app/email/
  agentmail_client.py
  models.py
  repository.py
  preprocessor.py
  service.py
  channel.py
  tools.py
```

Integration points:

- `app/api/webhooks.py` adds `/webhook/agentmail` when feature flag is enabled.
- `app/agent/tools/email.py` registers email tools when feature flag is enabled.
- Telegram confirmation should reuse the existing pending-action / confirmation
  mechanism where possible.
- `app/channels/registry.py` may need to support multiple named channels.
- `app/bot.py` should eventually generalize beyond Telegram-specific dispatch,
  but V1 can have an email-specific intake worker that asks for Telegram
  confirmation before calling `agent_run()`.

## Identity Resolution

Email sender maps to a user through `ChannelMapping`:

```text
ChannelMapping(
  channel="email",
  channel_user_id=normalized_from_email,
  user_id=<existing User.id>
)
```

Rules:

- normalize email by trimming whitespace and lowercasing
- use `From` for direct emails
- for forwarded emails, use the outer sender as the authenticated sender
- do not trust the forwarded original `From` as the HomeAgent user
- do not trust `Reply-To` for identity
- do not reply to `Reply-To` by default
- require a passing provider-authentication verdict for mapped senders before
  creating user-scoped webhook intake or Telegram prompts in production
- if AgentMail cannot expose a trustworthy auth verdict, webhook intake should
  persist metadata only and emit admin events; the user may trigger a trusted
  pull from Telegram instead
- if sender is unmapped and `EMAIL_CHANNEL_REQUIRE_MAPPED_SENDER=true`, do not
  create user-scoped intake or run the agent
- emit admin event and optionally notify an admin on unmapped sender

This matches the identity design in
`docs/user-identity-memory-link-design.md`: users remain Telegram-backed; email
is an additional mapped channel.

## Webhook Flow

Endpoint:

```text
POST /webhook/agentmail
```

Security:

- verify Svix headers: `svix-id`, `svix-timestamp`, `svix-signature`
- use the raw request body for verification
- enforce a tight timestamp tolerance, default `EMAIL_CHANNEL_SVIX_TOLERANCE_SECONDS=300`
- persistently dedupe `svix-id` as the webhook delivery id
- reject unverifiable payloads
- validate body size before parsing
- validate the AgentMail `inbox_id` matches configured `AGENTMAIL_INBOX_ID`
- dedupe by AgentMail `event_id` and message `message_id`
- only process `message.received`
- ignore `message.sent` / delivery events for agent runs to avoid reply loops
- reject or downgrade messages that fail email authentication checks
- fail closed for mapped senders: no user-scoped intake or Telegram prompt
  unless provider authentication passes in production
- never use forwarded inner headers, `Reply-To`, or display names for user
  identity

Processing:

1. Verify signature.
2. Parse and validate the minimal payload.
3. Persist or dedupe an `EmailMessage` row with status `RECEIVED`.
4. Return `200`/`204` only after the durable write succeeds.
5. A background worker claims the row and marks it `CLASSIFYING`.
6. If payload lacks `text`/`html`, fetch full message through AgentMail API.
7. Resolve sender email to `User.id`.
8. Verify mapped-sender authentication pass before user-scoped intake.
9. Apply intake throttles before expensive work.
10. Preprocess the email into compact intake content.
11. Build a proposed action / extracted candidates.
12. Send a Telegram confirmation prompt to the mapped user.
13. On Telegram confirmation, run `agent_run(...)` or a workflow-specific tool.
14. Mark `CONFIRMED`, `PROCESSED`, `IGNORED`, `RATE_LIMITED`, or schedule
    retry / dead-letter.

AgentMail webhook payloads are capped at 1 MB. If text/html are omitted, fetch
the full message via API using `inbox_id` and `message_id`.

## Data Model

Store operational email state in `cache.db`.

### `EmailMessage`

```python
id: str
provider: str                       # "agentmail"
provider_event_id: str | None
provider_delivery_id: str | None           # Svix svix-id
provider_message_id: str
provider_thread_id: str | None
provider_inbox_id: str

household_id: str | None
user_id: str | None
channel_user_id: str                # normalized sender email

from_email: str
to_json: str
cc_json: str
subject: str
timestamp: datetime | None

status: str                         # RECEIVED | CLASSIFYING | NEEDS_CONFIRMATION | CONFIRMED | PROCESSING | PROCESSED | IGNORED | RATE_LIMITED | FAILED_RETRYABLE | DEAD_LETTER
status_reason: str | None
attempt_count: int
next_attempt_at: datetime | None
locked_at: datetime | None
last_error: str | None

auth_status: str | None             # pass | fail | unknown
auth_details_json: str | None
reply_to_email: str | None

instruction_text: str
intake_summary_text: str
proposed_action_json: str | None
confirmation_id: str | None
confirmed_at: datetime | None
provider_metadata_json: str
raw_debug_json: str | None
raw_debug_expires_at: datetime | None
created_at: datetime
updated_at: datetime
processed_at: datetime | None
```

### `EmailAttachment`

V1 may store only metadata:

```python
id: str
email_message_id: str
provider_attachment_id: str
filename: str
content_type: str
size: int
inline: bool
```

Do not download attachment bodies in V1 unless a specific workflow needs them.

Recommended constraints:

- unique index on `provider_event_id` when present
- unique index on `provider_delivery_id` when present
- unique index on `(provider, provider_message_id)`
- index on `(status, next_attempt_at)` for retry workers
- store minimized/redacted provider metadata by default
- store raw provider payloads only when explicitly enabled or needed for failed
  debugging, bounded by `EMAIL_CHANNEL_RAW_DEBUG_RETENTION_DAYS`
- do not store raw email bodies by default

## Email Preprocessing

Email is noisy. The LLM should not get raw HTML + all quoted history by default.

Preprocessor goals:

- preserve the user's top instruction
- preserve useful forwarded booking details
- remove boilerplate, signatures, and tracking/marketing clutter where possible
- keep original content fetchable for tools/debugging
- avoid token spikes from long forwarded threads

Pipeline:

1. Prefer plain text body when available.
2. Convert HTML to text when text is missing or too sparse.
3. Split top user instruction from forwarded/quoted content.
4. Remove common email artifacts:
   - legal disclaimers
   - tracking pixel alt text
   - repeated quoted reply chains
   - unsubscribe/footer blocks
   - excessive blank lines
5. Detect domain-specific blocks:
   - flight numbers
   - airports / route lines
   - dates and times
   - booking references
   - passenger names
6. Build a compact intake summary:

```text
## Email Intake
From: kristian@example.com
Subject: Fwd: Your trip to Stockholm
Received: 2026-05-07T12:45:00+02:00

## User Instruction
Track these flights

## Extracted Signals
- possible flight: SK1461
- possible route: OSL -> CPH
- possible date: 2026-05-12

## Email Body Excerpt
<cleaned, bounded text>

## Attachments
- itinerary.pdf (application/pdf, 120 KB) [not downloaded]
```

The default max intake input should be controlled by
`EMAIL_CHANNEL_MAX_AGENT_CHARS`.

Prompt-injection guardrails:

- mark forwarded/quoted email content as untrusted source data
- only the mapped outer sender's top instruction should be treated as a user
  instruction
- do not let airline/vendor email text override tool policies, memory policy, or
  confirmation requirements
- if the user's top instruction is missing or ambiguous, the agent should ask a
  clarification instead of inferring a broad action from the forwarded content
- extracted entities should be presented as candidates, not facts, until a tool
  validates them

## Classification And Confirmation

Email-triggered intake should classify the message and ask for Telegram
confirmation before any meaningful action.

The classifier may be deterministic or use a cheap/background LLM, but it should
produce a bounded proposed action such as:

```json
{
  "kind": "track_flights",
  "confidence": "medium",
  "user_instruction": "Track these flights",
  "candidates": [
    {"flight_number": "SK1461", "date": "2026-05-12", "route": "OSL-CPH"}
  ]
}
```

Telegram confirmation example:

```text
I received an email from kristian@example.com:
"Track these flights"

Detected possible flights:
- SK1461, OSL -> CPH, 12 May

Track these flights?
```

If the intake burst threshold is exceeded, send a digest-style confirmation
instead of one Telegram message per email:

```text
I received 12 emails in the last 10 minutes. 3 look travel-related.
Review them?
```

## Agent Run Behavior

Confirmed email work should call the same `agent_run(...)` path as Telegram.

Recommended parameters:

```python
agent_run(
    text=confirmed_intake_text,
    user_id=mapped_user.id,
    household_id=mapped_user.household_id,
    channel_user_id=telegram_channel_user_id,
    trigger="email_confirmed",
    save_history=EMAIL_CHANNEL_SAVE_HISTORY,
)
```

Default `save_history=false` is intentional. The email repository stores the
message metadata and compact processed text; the normal chat history should not
be polluted with large forwarded email bodies.

If we want continuity for email threads later, add a separate email-thread
summary rather than putting every raw email into `ConversationTurn`.

Containment rules:

- email-triggered runs still use the per-user run lock
- normal policy gate rules apply to all high-impact tools
- Telegram confirmation is required for all email-originated meaningful actions
- if the email authentication status is `unknown` or `fail`, do not allow
  home-control writes, identity updates, memory writes, or outbound email tools
  from that run
- the first V1 use case should be low-impact workflow creation, such as
  extracting flight candidates and creating flight watches
- if a tool requires confirmation, prefer sending the confirmation to the
  user's primary Telegram channel rather than relying on email

## Email Reply Behavior

Email replies are optional in V1. The primary user-facing interaction should be
Telegram confirmation and Telegram result delivery.

If email acknowledgement is enabled, reply in the same AgentMail thread:

- use AgentMail reply API with `inbox_id` and `message_id`
- reply only to the mapped sender by default
- do not reply-all by default
- ignore `Reply-To` by default
- include concise acknowledgement/result only after Telegram confirmation
- include no raw secrets, booking references, or personal data unless necessary
  for the answer

For example:

```text
I have received this and sent a confirmation request in Telegram.
```

If the sender is unmapped:

- do not run the agent
- optionally send a short "not configured" reply only if explicitly enabled
- always emit an admin event

## Force Check Tool

Add an agent tool:

```python
check_email_now(
    limit: int = 10,
    lookback_hours: int | None = None,
    only_unprocessed: bool = True,
) -> str
```

Use cases:

- user says in Telegram: "I forwarded the flight email, check now"
- admin wants to retry webhook failures
- AgentMail webhook was temporarily unavailable

Behavior:

1. Query AgentMail for recent inbox messages.
2. Skip already-processed `provider_message_id` rows unless forced.
3. Apply the same sender mapping, throttling, and preprocessing as webhook flow.
4. Create or update intake rows up to `EMAIL_CHANNEL_FORCE_CHECK_LIMIT`.
5. Return a concise report.

This is a fallback, not the normal path. Webhooks remain primary.
It must not bypass Telegram confirmation.

The tool should be bounded by user context:

- it may only be invoked from trusted Telegram conversation context or an
  explicit admin-scoped context
- deny invocation from email-originated runs, email-derived prompt content, and
  any future email reply flow
- a normal user can force-check only messages mapped to their own email
  identities
- an admin may force-check all recent messages only through an admin command or
  explicit admin-scoped tool mode
- the tool must respect the same rate limits and dedupe rules as webhook
  ingestion

## Resilience And Retry Model

Email processing needs a small durable queue, implemented by `EmailMessage`
status fields in `cache.db`.

Recommended state machine:

```text
RECEIVED
  -> CLASSIFYING
  -> NEEDS_CONFIRMATION
  -> CONFIRMED
  -> PROCESSING
  -> PROCESSED
  -> RATE_LIMITED
  -> FAILED_RETRYABLE -> RECEIVED
  -> DEAD_LETTER
  -> IGNORED
```

Rules:

- claim pending rows with a short processing lease using `locked_at`
- if the process crashes while `PROCESSING`, a sweeper should release stale
  locks back to `RECEIVED`
- persistently dedupe the Svix delivery id, AgentMail event id, and AgentMail
  message id so provider retries cannot duplicate confirmations or actions
- reject signed payloads outside the configured Svix timestamp tolerance
- retry transient AgentMail API, preprocessing, and reply failures with
  exponential backoff and jitter
- do not retry deterministic failures such as unmapped sender, bad auth,
  unsupported event type, or body too large
- after `EMAIL_CHANNEL_MAX_PROCESSING_ATTEMPTS`, move to `DEAD_LETTER`
- `check_email_now` can requeue selected `FAILED_RETRYABLE` or `DEAD_LETTER`
  rows only when explicitly requested by an admin
- duplicate webhooks should update delivery metadata but must not trigger
  another Telegram confirmation, agent run, or reply

This keeps provider retries, app restarts, and Docker restarts from losing email
work or duplicating side effects.

## Intake Throttling And Burst Protection

There are three separate throttle layers:

1. Cloudflare edge throttles before HomeAgent receives the request.
2. Webhook intake throttles before durable rows are accepted or full bodies are
   fetched.
3. Classification/notification throttles before LLM calls or Telegram prompts.

Recommended behavior:

- always accept and dedupe legitimate provider retries when possible
- if global intake is too high, store minimal metadata and mark lower-priority
  rows `RATE_LIMITED` instead of fetching full bodies
- classify mapped senders before unmapped senders
- suppress repeated unmapped sender notifications into a single admin summary
- if one mapped user receives a burst, send a Telegram digest instead of many
  individual confirmation prompts
- do not let `check_email_now()` bypass throttles; it can prioritize the
  requesting user's own mapped emails within the normal caps

Example digest:

```text
I received 12 emails in the last 10 minutes. 3 look travel-related.
Review them?
```

## Rate Limits And Abuse Controls

Email is internet-facing, so V1 should include simple hard limits:

- reject webhook bodies over `EMAIL_CHANNEL_MAX_RAW_BODY_BYTES`
- process at most `EMAIL_CHANNEL_RATE_LIMIT_PER_SENDER_PER_HOUR` messages from
  one sender
- process at most `EMAIL_CHANNEL_RATE_LIMIT_PER_USER_PER_HOUR` messages for one
  mapped user
- cap full-message fetches per hour to avoid API quota burn from large messages
- cap attachments by count and size metadata; do not fetch bodies in V1
- collapse repeated unmapped sender events into a summarized admin event after
  the first few occurrences
- never auto-create users or mappings from email
- never run the agent for mailing lists, auto-replies, bounces, delivery status
  notifications, or messages from the AgentMail address itself

Auto-detection signals to ignore:

- `Auto-Submitted: auto-replied` or similar
- `Precedence: bulk|junk|list`
- delivery status notification content types
- messages where the sender equals `AGENTMAIL_ADDRESS`
- messages already marked as sent by the agent

## Admin And Observability Events

Emit:

| Event | Meaning |
| --- | --- |
| `email.webhook_received` | AgentMail webhook accepted |
| `email.webhook_rejected` | Signature/body/event validation failed |
| `email.message_received` | New message metadata persisted |
| `email.sender_unmapped` | From address did not map to a user |
| `email.message_duplicate` | Event/message already processed |
| `email.full_fetch_started` | Webhook lacked body; API fetch started |
| `email.full_fetch_failed` | Full message fetch failed |
| `email.auth_failed` | Sender mapped but email authentication failed |
| `email.rate_limited` | Sender/user exceeded processing limits |
| `email.preprocessed` | Compact agent input built |
| `email.classified` | Proposed action / candidates extracted |
| `email.confirmation_requested` | Telegram confirmation sent |
| `email.confirmation_digest_requested` | Burst summarized into a Telegram digest |
| `email.confirmed` | User confirmed email-originated action in Telegram |
| `email.agent_triggered` | Agent run started after Telegram confirmation |
| `email.retry_scheduled` | Retryable failure scheduled |
| `email.dead_lettered` | Processing exhausted retries |
| `email.reply_sent` | Email reply sent |
| `email.reply_failed` | Email reply failed |
| `email.force_check_started` | Tool/API-triggered inbox check started |
| `email.force_check_completed` | Tool/API-triggered inbox check completed |

Admin later:

- recent email messages
- mapped/unmapped senders
- processing status
- latest failure
- webhook health
- backlog size by status
- oldest unprocessed message age
- retry/dead-letter counts
- AgentMail API quota/error counters where available

SSE payloads must be metadata-first:

- include `email_message_id`, status, sender domain, mapped user id/name, and
  short subject excerpt
- do not include full body, raw headers, booking references, passenger details,
  attachments, or full raw provider payload by default
- show sensitive detail only in an authenticated admin drill-down if later
  implemented

## Security And Privacy

- AgentMail API token lives only in `.env`.
- Verify Svix signatures in production.
- Reject Svix signatures outside the configured timestamp tolerance.
- Persistently dedupe `svix-id`.
- Do not expose AgentMail raw payloads in prompts or admin default views.
- Store minimized/redacted provider metadata by default, not raw email bodies.
- Do not process email from unmapped senders.
- Do not trust forwarded inner headers as identity.
- Do not trust `Reply-To` as identity or default recipient.
- Require provider/email authentication pass for mapped-sender webhook intake in
  production.
- Do not add forwarded email content to episodic memory by default.
- Use body-size limits and retention cleanup.
- Avoid downloading attachments unless explicitly needed.
- Treat forwarded email body as untrusted data for prompt-injection purposes.
- Require Telegram confirmation for all meaningful actions triggered from email.
- Keep the existing policy gate in addition to Telegram confirmation for
  high-impact actions.
- Redact or suppress booking references, passenger details, addresses, and other
  sensitive travel data in logs and SSE events.

## Retention

Default:

- keep `EmailMessage` metadata and compact derived text for
  `EMAIL_CHANNEL_RETENTION_DAYS`
- do not store raw email bodies by default
- when raw debug payloads are explicitly stored, delete them after
  `EMAIL_CHANNEL_RAW_DEBUG_RETENTION_DAYS`
- keep attachment metadata for the same duration
- do not store attachment bodies in V1

Emails can contain sensitive travel data, booking references, addresses, and
personal details. Retention should be shorter than normal conversation memory.

## Failure Handling

| Failure | Behavior |
| --- | --- |
| bad signature | reject, emit `email.webhook_rejected` |
| stale Svix timestamp | reject, emit `email.webhook_rejected` |
| duplicate Svix delivery id | acknowledge, update metadata, do not process again |
| wrong inbox id | reject or ignore, emit `email.webhook_rejected` |
| auth unavailable for webhook sender | metadata-only admin event; no user-scoped intake |
| auth failure for mapped sender | mark ignored, emit `email.auth_failed`; no Telegram prompt |
| unmapped sender | persist minimal metadata, do not create user intake or run agent |
| sender/user rate limited | mark retry/ignored depending on limit, emit `email.rate_limited` |
| missing body | fetch full message through AgentMail API |
| API fetch failure | mark retryable, schedule backoff retry |
| preprocessing too large | truncate to configured limit and mention truncation |
| Telegram confirmation send failure | mark retryable and do not run agent |
| Telegram confirmation denied/expired | mark ignored, do not run agent |
| agent run failure after confirmation | mark retryable only if transient; otherwise dead-letter |
| reply failure | retry send only; do not rerun agent automatically |
| process crash mid-run | release stale processing lease and retry idempotently |
| duplicate webhook | acknowledge, update metadata, do not resend confirmation or rerun agent |

## Phased Implementation

### Phase 0: AgentMail Spike

Verify with real account:

- `.env` token works
- inbox ID/address known
- webhook can be registered for `message.received`
- Svix secret is available
- webhook payload includes enough body text for normal forwarded emails
- full message fetch works when body is omitted
- reply in same thread works
- payload or fetch exposes enough headers/authentication metadata to evaluate
  SPF/DKIM/DMARC or equivalent trust
- AgentMail retry behavior on non-2xx webhook responses is understood
- Cloudflare ingress accepts AgentMail webhook delivery and preserves the raw
  body required for Svix verification
- the existing Telegram confirmation mechanism can carry an email intake
  confirmation payload safely

### Phase 1a: Webhook Skeleton

- `FEATURE_EMAIL_CHANNEL` config flag
- AgentMail client wrapper (httpx, no official SDK)
- `/webhook/agentmail` endpoint added to `app/api/webhooks.py` when flag enabled
- Svix signature verification on raw body
- Svix timestamp tolerance enforcement
- body size limit check
- `inbox_id` validation
- `EmailMessage` + `EmailAttachment` models in `cache.db`
- Alembic migration
- persist or dedupe incoming `EmailMessage` row before returning 200
- persistent dedupe on `svix-id`, `provider_event_id`, and `(provider, provider_message_id)`
- auto-detection and rejection of auto-replies, bounces, and loopback messages
- smoke-testable with a real AgentMail delivery before any side effects

### Phase 1b: End-to-End Path

- sender mapping through `ChannelMapping(channel="email")`
- fail-closed auth behavior: if AgentMail exposes a trust verdict, require pass
  for user-scoped intake; if not, mark `auth_status=unknown` and proceed with
  Telegram confirmation as the sole trust gate
- `EmailIntakeConfirmation` model in `cache.db` (separate from `PendingAction`)
- `confirmation_id` on `EmailMessage` points to `EmailIntakeConfirmation.token`
- Telegram confirmation uses callback prefix `email_confirm:` / `email_cancel:`
  handled in a new branch in `telegram.py _handle_callback_query`
- on confirmation: look up user's Telegram `ChannelMapping` to get
  `channel_user_id`, then call `agent_run(text=intake_text, trigger="email_confirmed", ...)`
- compact email preprocessor (plain-text extraction, forwarded content split,
  boilerplate removal, bounded intake summary)
- basic classifier (deterministic signal extraction: flight numbers, routes, dates)
- Telegram confirmation prompt
- `check_email_now` agent tool (same sender mapping, throttling, and dedupe as
  webhook path; does not bypass Telegram confirmation)
- non-critical config values hardcoded as module constants; only essential
  settings in `.env` (see Configuration section)

### Phase 1c: Operational Hardening

- background worker with `locked_at` processing lease
- stale-lock sweeper (release leases older than processing timeout)
- `FAILED_RETRYABLE` → `RECEIVED` retry with exponential backoff and jitter
- `DEAD_LETTER` after `EMAIL_CHANNEL_MAX_PROCESSING_ATTEMPTS`
- sender/user/global intake throttles
- burst digest: collapse multiple emails from one user into a single Telegram summary
- admin SSE events for the full event list in the Admin and Observability section

### Phase 2: Workflow Polish

- flight-confirmation extraction helpers
- attachment support for itinerary PDFs
- better thread summaries
- admin email channel view
- optional email acknowledgement/reply after Telegram confirmation
- optional outbound email tool for selected workflows

Attachment extraction must not begin until a separate security envelope is in
place:

- strict attachment count and byte-size limits
- content-type allowlist plus magic-byte verification
- server-generated storage ids; never trust attachment filenames as paths
- no inline serving of original attachments from the admin UI
- sandboxed parsing process or isolated worker for PDFs and office documents
- extracted text stored separately from raw files with its own retention window
- no attachment body persistence unless explicitly required by the workflow
- malware scanning or equivalent quarantine if attachment storage is added

## Resolved Decisions

1. **Confirmation flow shape**: Use a new `EmailIntakeConfirmation` entity in
   `cache.db`, not an extension of `PendingAction`. The two flows have different
   shapes — policy-gate confirmations re-execute a Homey tool; email intake
   confirmations call `agent_run()` with pre-built intake text. Separate callback
   prefixes (`email_confirm:` / `email_cancel:`) and a new handler branch in
   `telegram.py` keep the concerns clean. `EmailMessage.confirmation_id` points
   to the `EmailIntakeConfirmation` token.

2. **Auth verdict handling**: Auth results are available via the `headers` dict
   in the message payload. Amazon SES adds an `Authentication-Results` header
   containing `spf=pass/fail/none`, `dkim=pass/fail/none`, and
   `dmarc=pass/fail/none`. The preprocessor should parse this header to set
   `auth_status` on `EmailMessage`. AgentMail also drops emails that explicitly
   fail authentication before delivery, so absence of the header or an `unknown`
   result should be treated conservatively. Parse with:
   `email.utils` + regex on `headers.get("Authentication-Results", "")`.
   Telegram confirmation remains the user-facing trust gate regardless of
   `auth_status`.

3. **Phase 1 is staged as 1a / 1b / 1c**: 1a = webhook skeleton + persist,
   smoke-testable. 1b = end-to-end path including Telegram confirm + agent_run +
   `check_email_now`. 1c = retry worker, throttles, burst digest, admin events.

4. **`check_email_now` lands in Phase 1b**: available as soon as the end-to-end
   path exists, useful while webhook reliability is being validated.

5. **Unmapped senders**: silently ignored, admin event only. No auto-reply.

6. **Email history**: not saved to `ConversationTurn` by default
   (`save_history=false`). Email repository stores minimized metadata and compact
   derived text.

7. **Attachments**: metadata only in V1. See Phase 2 security envelope
   requirements before any body download.

8. **Reply-all**: never for V1. Replies go only to the mapped outer `From`.

9. **Agent response routing**: on confirmation, look up the user's
   `ChannelMapping(channel="telegram")` to get `channel_user_id` before calling
   `agent_run()`. The channel registry returns the single active Telegram channel
   for V1; email does not register as a separate channel.

10. **Config discipline**: non-critical settings (retry timing, burst thresholds,
    raw debug retention, etc.) are hardcoded as module constants in Phase 1 and
    promoted to `.env` settings only if operational experience shows they need
    tuning.

## Current Judgement

Design review complete. Implementation-ready after Phase 0 confirms AgentMail
webhook payload shape and Cloudflare raw-body preservation for Svix verification.

The key architectural choice is to treat email as untrusted intake only: email
can wake the system, persist a candidate, extract useful context, and ask the
mapped user in Telegram. Telegram confirmation is required before `agent_run()`,
flight-watch creation, memory writes, task creation, or other meaningful side
effects. The existing policy gate applies on top of Telegram confirmation for
high-impact tools.

Implementation order: Phase 0 spike → 1a (webhook + persist) → 1b (full path +
`check_email_now`) → 1c (hardening) → Phase 2 (workflow polish).
