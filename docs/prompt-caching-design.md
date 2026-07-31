# Prompt Caching Design

Status: implemented (Steps 1-4). `pydantic-ai` upgraded 2.0.0 → 2.21.0 as
part of this work. Anthropic gets real explicit `cache_control` breakpoints;
OpenAI/GPT-5.6 is deliberately kept in `mode: "explicit"` with no breakpoints
placed (caching off), since pydantic-ai has no system-message-level
breakpoint hook for it — see the OpenAI decision-gate section below, which
is now the historical record of that resolution rather than an open
decision. `feature_prompt_caching` in `.env` reverts both providers to the
old plain `ModelSettings(max_tokens=...)` if needed.
Last code check: 2026-07-30
Runtime entry points touched by this design: `app/agent/agent.py`,
`app/agent/context.py`, `app/agent/runner.py`, `app/agent/llm_router.py`,
`app/agent/prompts.py`, `app/agent/skills.py`, `app/models/cache.py`
(`AgentRunLog`), `app/control/api.py` (`/admin/stats`)

## Purpose

Every `agent_run()` call (interactive messages, scheduled prompts, reminders,
task resumes — all routed through the single `assemble_context()` →
`run_conversation()` path) re-sends a large, mostly-unchanged payload to the
model: `persona.md` + `instructions.md` (~11KB) and the full tool schema set
(local `@agent.tool` functions + Homey/Prometheus/tools-mcp MCP toolsets,
~50–80+ tools). None of this is cached today. Both Anthropic and OpenAI offer
prompt caching that would cut input-token cost and latency on this static
majority of every call, but it requires a specific structure that the current
single-concatenated-string system prompt doesn't have. **The two providers
are not symmetric, and the gap is worse than "OpenAI just needs a follow-up":**
the Anthropic path (explicit `cache_control` breakpoints via pydantic-ai) is
fully reachable and delivers the stated goal today. The OpenAI path — the
actual primary in the checked deployment's `.env` (`MODEL_PRIMARY=gpt-5.6`)
— cannot deliver meaningful caching benefit through pydantic-ai's currently
exposed settings at all: GPT-5.6's only reachable mode (implicit) places its
cache breakpoint after the entire prior request, including our necessarily
per-call-dynamic system content, so the reusable static prefix this doc
builds is never actually hit. Reaching the stated goal on GPT-5.6 requires
either a pydantic-ai upgrade or a custom mapper exposing explicit breakpoints
— see the OpenAI section's decision gate before implementing anything on
this provider, and do not enable OpenAI-side caching settings by default
without one of those in place.

This doc covers what's actually available in the installed stack
(`pydantic-ai==2.0.0`, `anthropic==0.112.0`, `openai==2.44.0` — confirmed by
reading the installed `pydantic_ai.models.anthropic` / `.openai` source, not
assumed), what needs to change in context assembly to make caching effective,
and what stays out of scope for the first pass.

Goals:

- Cache the static system content (persona + instructions template bodies)
  and the tool/skill schema set, since these are identical across calls and
  across users.
- Make the static/dynamic split easy to keep correct as tools and skills are
  added — not a manually-maintained boundary that silently rots.
- Get cache-hit visibility into `AgentRunLog` / `/admin/stats`, since without
  it the savings are invisible and regressions (e.g. a change that breaks the
  cacheable prefix) would go unnoticed.

Non-goals (first pass):

- Caching `message_history` / recent turns — this is the one part of the
  payload that's expected to change every call (new turn appended each time).
  Anthropic's automatic caching (see below) can still pick up a stable
  sub-prefix of history for free; no explicit handling needed here.
- Background-task agents (`MEMORY_EXTRACTION`, `SUMMARIZATION`,
  `WORLD_MODEL_EXTRACTION`) — lower call volume, separate short-lived
  `Agent` instances, not user-facing latency-sensitive. Revisit later if
  their aggregate cost matters.
- Embeddings (`model_embedding`) — not a caching-relevant API shape.

---

## What pydantic-ai 2.0.0 actually supports (confirmed from source)

This is more built-in than expected — no need to hand-roll `cache_control`
plumbing.

### Anthropic (`pydantic_ai.models.anthropic.AnthropicModelSettings`)

Three independent settings, all passed via `model_settings=` on `agent.run()`
(a plain dict / `ModelSettings` — extra provider-specific keys are ignored by
non-Anthropic models, so these are safe to always set regardless of which
provider ends up handling the call):

- `anthropic_cache_instructions: bool | Literal['5m', '1h']` — adds
  `cache_control` after the **last static instructions block**. Pydantic-ai
  is aware of the difference between `Agent(instructions=...)` (static,
  cacheable, not stored in message history) and `@agent.system_prompt`
  (dynamic, re-run and re-concatenated every call) — when both are present,
  it places the cache breakpoint correctly at the boundary between them.
- `anthropic_cache_tool_definitions: bool | Literal['5m', '1h']` — adds
  `cache_control` to the last tool in the `tools` array, which caches
  everything before it (i.e. the whole tool schema block) as one unit.
- `anthropic_cache: bool | Literal['5m', '1h']` — Anthropic's newer
  *automatic* prefix caching: a single top-level flag, no manual breakpoint
  placement. The server finds the longest cacheable prefix itself and moves
  the breakpoint forward as the conversation grows (useful for reusing a
  stable prefix of `message_history` as it accumulates turns). It still only
  helps if the actual bytes before the boundary are identical call to call,
  so the context-assembly reordering below is still required. **This is not
  an alternative to the two explicit settings above — pydantic-ai supports
  combining them.** Anthropic's automatic breakpoint just consumes 1 of the
  4 available cache-point slots, leaving 3 for explicit ones (pydantic-ai's
  `_limit_cache_points` already enforces `MAX_CACHE_POINTS = 3` when
  automatic caching is on, vs. 4 when it's off).
- Explicit `CachePoint` markers (from `pydantic_ai.messages`) can be placed
  inside `UserPromptPart.content` for finer-grained control, but the two
  settings above cover our case without touching message construction.
- Anthropic hard-caps at 4 cache breakpoints per request; pydantic-ai already
  tracks and trims automatically (`_limit_cache_points`) so we don't need to
  count them ourselves.

### Anthropic caching mechanics that constrain this design (verified)

Facts from Anthropic's current caching model that the plan must respect:

- **Prefix match, rendered in order `tools` → `system` → `messages`.** Any
  byte change invalidates everything after it. A breakpoint on the last
  static instructions block therefore caches *tools + static instructions
  together* — which is why the tool-definitions breakpoint plus the
  instructions breakpoint cover our whole static payload.
- **Minimum cacheable prefix is model-dependent and silently enforced** — a
  prefix below the minimum doesn't error, it just never caches
  (`cache_creation_input_tokens: 0`). Current-generation Opus/Haiku-class
  models require ~4096 tokens; Sonnet-class ~1024–2048. Our
  `persona.md`+`instructions.md` alone (~11KB ≈ ~3K tokens) is *marginal on
  its own* — but tools render before system, and the ~50–80 tool schemas
  push the combined prefix comfortably past any minimum. Consequence: **the
  tool-definitions cache is the load-bearing one; always enable both
  settings together**, and don't evaluate the instructions cache in
  isolation.
- **Cache TTL and write pricing**: cache reads cost ~0.1× base input; writes
  cost **1.25× for the default 5-minute TTL, 2× for 1-hour TTL**. Break-even
  is ~2 requests within TTL for 5m, ~3 for 1h. Both pydantic-ai settings
  accept `'5m' | '1h'` instead of `True`. For HomeAgent's traffic shape —
  bursty interactive conversations (well inside 5m) plus scheduled prompts
  hours apart — start with the default 5m: interactive bursts are where the
  volume is, and a scheduled prompt hours after the last run misses either
  TTL anyway. Only consider `'1h'` if `AgentRunLog` timestamps show a
  meaningful fraction of runs falling in the 5m–60m gap.
- **The cache is model-scoped.** A failover from primary to fallback
  (`agent_run()`'s model chain) starts cold on the fallback model — expected,
  no handling needed, but don't misread a failover run's zero
  `cache_read_tokens` as a regression.
- **Usage accounting**: `input_tokens` in Anthropic responses is the
  *uncached remainder only*. Total prompt size = `input_tokens +
  cache_creation_input_tokens + cache_read_input_tokens`. Any dashboard that
  today treats `input_tokens` as "total input" will appear to drop sharply
  once caching lands — `/admin/stats` must sum all three or the token graphs
  will mislead.
- **20-block lookback (relevant only if `anthropic_cache` automatic mode is
  later enabled)**: a message-level breakpoint only searches back 20 content
  blocks for a prior cache entry. Our tool-heavy runs can easily add >20
  blocks in one turn, which would make automatic history caching silently
  miss. Another reason the first pass sticks to the two explicit
  instruction/tool breakpoints and treats automatic mode as a measured
  follow-up experiment.

### OpenAI (`pydantic_ai.models.openai.OpenAIChatModelSettings`) — verified against OpenAI's current docs

Caching is automatic server-side for any prompt ≥1024 tokens with an
identical prefix (prefix match determined by a hash of roughly the first 256
tokens — same "stable content first" principle as Anthropic). Confirmed
directly from OpenAI's prompt-caching guide (fetched during this review, not
assumed), which matters here because **`model_primary` in the checked
deployment's `.env` is `gpt-5.6`** — this is the provider actually carrying
production traffic today, not the fallback.

**OpenAI's caching economics changed as of GPT-5.6 and are no longer
provider-symmetric with pre-5.6 models:**

| Aspect | Pre-GPT-5.6 | GPT-5.6+ (our current primary) |
| --- | --- | --- |
| Cache writes | Free | **1.25× the uncached input rate** — same premium as Anthropic's 5m TTL |
| Cache reads | Discounted | Discounted (reported as `cached_tokens`) |
| Retention control | `'in_memory'` (5–10 min idle, up to 1h) or `'24h'` extended | `prompt_cache_options.ttl` — **minimum 30 minutes**, described as the only supported value shape; the old `'in_memory'`/`'24h'` enum does not appear to be how GPT-5.6+ retention is configured |
| Cache key | Optional hint | **Effectively required** for reliable cache matching, per OpenAI's own guidance |
| Manual breakpoints | Not available | Available via `prompt_cache_breakpoint` + `prompt_cache_options` — see below |

**GPT-5.6's `prompt_cache_options.mode` — precise mechanics, verified
against OpenAI's docs (not from pydantic-ai source, since pydantic-ai
doesn't expose this at all):**

- **`mode: "implicit"` is the default.** OpenAI automatically places a
  breakpoint on the latest message and still honors any explicit breakpoints
  you've added. This is the ≥1024-token automatic behavior — it works with
  **no code change**, same as pre-5.6.
- **`mode: "explicit"` disables automatic placement entirely.** Only
  content blocks carrying a `prompt_cache_breakpoint` marker are used for
  cache reads/writes. Per OpenAI's own docs: *"If the conversation contains
  no explicit breakpoints, the request does not use prompt caching or incur
  cache-write charges."* — i.e. setting `mode: "explicit"` without also
  placing breakpoints doesn't degrade caching, it **turns it off entirely**.
- GPT-5.6 also requires `prompt_cache_key` for "more reliable matching" in
  **both** implicit and explicit mode — not just explicit mode.

**Gap, as installed at review time (`pydantic-ai==2.0.0`) — since resolved
by upgrading to `2.21.0`.** The installed 2.0.0 `OpenAIChatModelSettings`
only had `openai_prompt_cache_key: str` and `openai_prompt_cache_retention:
Literal['in_memory', '24h']` — no `prompt_cache_options` field, no
`prompt_cache_breakpoint` marker. `pydantic-ai==2.21.0` (confirmed by
installing it into a scratch venv and reading the source directly, not from
changelogs) adds `openai_prompt_cache_options: {mode, ttl}` and maps
`CachePoint` to an explicit breakpoint — but, as detailed in the next
section, only for `CachePoint` placed inside **user-message content**, not
the system/instructions message. So the practical gap remains even after
upgrading: **there is still no way to place a breakpoint after our static
instructions block specifically.** See the resolved decision gate below for
what this implementation actually does about it.

### Why implicit mode likely does not help this architecture at all

**Correction to an earlier draft of this doc**, which claimed implicit mode
"still auto-caches ≥1024-token identical prefixes, which is exactly what the
static-prefix restructuring produces." That's wrong for our specific request
shape, and the distinction matters:

GPT-5.6's implicit-mode breakpoint is placed at **the latest message** — it
hashes the entire prior prefix (tools + system + full message history) as
one unit and looks for a byte-identical match to a previous request's prefix
at that same boundary. It does **not** give partial credit for a static
sub-portion sitting underneath dynamic content, the way Anthropic's explicit
`cache_control` breakpoint does. Our restructuring (Steps 1–2) separates
static from dynamic at the **pydantic-ai/Anthropic conceptual level**
(`Agent(instructions=...)` vs. `system_prompt`) — but on the OpenAI wire
format, both are rendered into the request's `system`/message content, and
GPT-5.6's single automatic breakpoint sits *after* all of it, at the point
where the new user message is appended. Since our dynamic content
(`<time_context>`, relevant memories, active task) changes on **every**
call, the prefix up to that breakpoint is never byte-identical to a prior
request, even though the static instructions and tool schemas underneath it
haven't changed.

Practical consequence: under implicit mode, expect `cached_tokens` to be
**~0 on essentially every call**, while `cache_write_tokens` may still be
charged at the 1.25× premium on every request that clears the 1024-token
threshold (since implicit mode auto-writes to cache whenever eligible,
independent of whether a future read will ever land). That is a **plausible
net cost increase**, not a neutral "no benefit" — the write premium is paid
repeatedly against a prefix that structurally can never be re-hit. This is
the reverse of the doc's stated goal for this provider.

### Decision gate before implementing the OpenAI side — resolved

**Checked directly against the actual installed-candidate version
(`pydantic-ai-slim[openai]==2.21.0`, installed into a scratch venv and
inspected via `inspect.getsource`, not assumed from changelogs):**

`OpenAIChatModelSettings` in 2.21.0 does add `openai_prompt_cache_options:
OpenAIPromptCacheOptions` (`{mode: 'implicit'|'explicit', ttl: '30m'}`) and
does map `CachePoint` into an explicit `prompt_cache_breakpoint` — **but only
when a `CachePoint` is placed inside `UserPromptPart.content`** (confirmed by
reading `_map_user_prompt_content_item`, which gates the mapping behind
`self.profile.get('openai_supports_prompt_cache_breakpoints', False)` and
calls `_add_openai_prompt_cache_breakpoint(content)` on the **user-message
content list**). There is no equivalent hook for the system/instructions
message — pydantic-ai has no `openai_cache_instructions` /
`openai_cache_tool_definitions` counterpart to the Anthropic settings.
**Upgrading pydantic-ai does not unlock caching our static
persona/instructions/tools block on GPT-5.6.** Decision-gate option 1, as
originally framed, does not fully resolve — only a custom mapper (option 3)
could place a breakpoint at that boundary, and it isn't worth building for
this pass (see below).

**What the upgrade *does* give us, and why it's still worth taking:** an
explicit, verifiable way to turn implicit-mode caching **off**.
`openai_prompt_cache_options={"mode": "explicit"}` with zero `CachePoint`
markers placed anywhere means, per OpenAI's own docs (quoted above): *"the
request does not use prompt caching or incur cache-write charges."*

This matters because **omitting the setting is not the same as disabling
caching** — implicit mode is OpenAI's server-side default when the field is
absent entirely, meaning **today's runtime, unmodified, is almost certainly
already writing cache entries on every GPT-5.6 call that clears 1024 tokens**
(`instructions.md` alone is ~9KB) **and almost certainly never reading
them**, since the system content has always been dynamically rendered
per-call. This predates and is independent of this design doc's changes —
it's the current production behavior. The earlier draft's recommendation to
"leave OpenAI settings unset" was therefore backwards: unset is the
*expensive* default, not the safe one.

**Revised plan — do this, not the original three-way gate:**

1. Upgrade `pydantic-ai` to `2.21.0` (real gains beyond caching — see the
   version-check output — and it's the release that lets us actively opt
   out, which we need). Re-run the full test suite and the pydantic-ai 1.x →
   2.0-style regression check before relying on it (breaking changes have
   bitten this codebase before — see `CHANGELOG.md`).
2. Set `openai_prompt_cache_options={"mode": "explicit"}` in `run_conversation()`'s
   model settings, unconditionally, with no `CachePoint` placed anywhere in
   our message construction. This deliberately and verifiably stops GPT-5.6
   implicit-mode cache writes — removing a cost that was likely already being
   paid, not just avoiding a hypothetical future one.
3. Do **not** set `openai_prompt_cache_key` or `openai_prompt_cache_retention`
   — both are meaningless once caching is explicitly turned off, and setting
   them would be dead configuration.
4. Leave a documented follow-up (not part of this implementation pass): if
   OpenAI's API ever adds a way to mark a cache boundary on the system
   message itself (equivalent to Anthropic's `anthropic_cache_instructions`),
   revisit; until then, a custom mapper (the old option 3) is not worth
   building — the whole point of using pydantic-ai here is to avoid
   hand-rolling provider wire formats, and the payoff without a system-level
   breakpoint mechanism is zero regardless of how the request is built.

Anthropic is unaffected by any of this — `anthropic_cache_instructions` /
`anthropic_cache_tool_definitions` already place the breakpoint at the
correct boundary (end of the static `instructions`) and deliver the doc's
stated goal on that provider today.

**`openai_prompt_cache_key` — not used in this implementation.** OpenAI's
guidance (reuse a key across requests sharing a long prefix, ~15 rpm per key)
only matters when caching is actually active. Since we're explicitly turning
GPT-5.6 caching off (no reusable static prefix reachable through pydantic-ai
today), the key is moot — do not set it. If OpenAI ever exposes a
system-level breakpoint and this gets revisited, the app-level-vs-household-id
tradeoff from the Anthropic cache-key-versioning section applies the same way
there.

`CachePoint` markers, when placed inside `UserPromptPart.content`, now map to
an explicit OpenAI breakpoint on 2.21.0 too (not just Anthropic) — but this
implementation doesn't place any, since our static content lives in the
system/instructions message, not user content — `CachePoint` markers are
simply unused in our message-construction code paths under this design.

### Provider-agnostic implication

`model_primary`/`model_fallback` are `.env`-driven (`app/config.py`
defaults to Claude primary; the checked deployment's `.env` currently has
GPT-5.6 primary with `claude-sonnet-5` fallback — see
`app/agent/llm_router.py get_model_chain()`). This design must not assume a
fixed provider order — **the context-assembly reordering below benefits
whichever provider is active**, and the Anthropic-specific `model_settings`
keys only take effect when a run actually lands on the Anthropic model
(today: on failover, or if `.env` is reconfigured back to Claude-primary).
Both sets of settings should be set together and unconditionally — each
provider ignores what it doesn't understand.

### Cache-key versioning

This applies to Anthropic only in this implementation (OpenAI caching is
explicitly disabled — see above, so there's no OpenAI cache key to version).
Track a `STATIC_PROMPT_CACHE_VERSION` constant, bumped whenever the static
`instructions` content or the registered toolset changes meaningfully. This
isn't needed for correctness — `/reload` rebuilds the local
`_conversation_agent` singleton correctly, and the **provider-side cache is
external**: an edit to `instructions.md` or a newly-registered tool naturally
produces a byte-different prefix and simply misses the old cache with no
explicit invalidation needed. It's needed for **attribution** — without a
version label there's no way to tell, from `/admin/stats`, whether a
hit-rate change was caused by "we changed the prompt" or "cache just
expired."

### Usage/cost tracking already exists, just isn't read

`pydantic_ai.usage.RunUsage` already has `cache_write_tokens` and
`cache_read_tokens` fields (confirmed via `RunUsage.__init__` signature) —
Anthropic's `cache_creation_input_tokens`/`cache_read_input_tokens` are
already mapped into these by pydantic-ai's usage extraction. `runner.py`
currently only reads `usage.input_tokens`/`usage.output_tokens`
(`app/agent/runner.py`, the block after `result.usage`) and
`_write_run_log()` only persists `{input, output}` into `AgentRunLog.
tokens_used`. No new usage-tracking mechanism needs to be built — just wire
the two existing fields through.

---

## What has to change in context assembly

The blocker isn't API support — it's that `app/agent/agent.py`'s
`@a.system_prompt` closure (lines ~80–127) builds **one string** by
concatenating, in order: a per-call `<time_context>` JSON block, then
`persona.md` + `instructions.md` (already rendered with per-call
`format_map` substitutions — `current_date`, `current_time`, `user_name`,
etc.), then a suffix of profile/world-model/task/summary/**memory**/skills
sections. Two problems:

1. The dynamic time block sits *first*, so even a byte-perfect cache on
   everything after it can't be reached — Anthropic/OpenAI both cache by
   matching from the start of the prompt forward.
2. `persona.md`/`instructions.md` are template-rendered with per-call
   variables (current time, names) before being cached-together with
   variable-free content, so the "static" block is actually different every
   call anyway.

### Proposed split

Move the truly static text into `Agent(instructions=...)` (pydantic-ai's
mechanism for content that's cacheable and *not* re-persisted into message
history), and keep everything call-varying in `@agent.system_prompt`
(unchanged mechanism, still concatenated per call):

- **Static (→ `instructions`)**: `instructions.md` is already variable-free
  (behavior rules only) and can move as-is. `persona.md` is NOT — its opening
  two lines (`prompts/persona.md:16-17`) are prose that directly interpolates
  `{agent_name}`, `{household_name}`, `{user_name}`: *"You are {agent_name},
  the AI assistant for the {household_name} household. You are currently
  speaking with {user_name}."* These can't simply be left unrendered (the
  model would see literal `{agent_name}` text) or silently dropped from the
  static block (the model needs to know who it is and who it's talking to).
  The fix is to split `persona.md` itself: keep the tone/style/behavior body
  (generic, no placeholders) in the static block, and emit a small dynamic
  **identity block** — the one or two rendered identity sentences — as the
  *first* section of the dynamic `system_prompt` suffix, immediately after
  the static `instructions`. `current_date`/`current_time`/`timezone` stay
  dynamic regardless (they're the part that changes every call). This is a
  content change to `persona.md` plus a small addition to
  `app/agent/prompts.py`/`agent.py`, not just a rendering-order change.
- **Static (→ tool schemas)**: no code change needed — MCP toolsets and local
  `@agent.tool` registrations are already built once at agent-construction
  time (`get_conversation_agent()` singleton, `app/agent/agent.py`) and don't
  vary per call. `anthropic_cache_tool_definitions=True` covers this for
  free once set.
- **Dynamic (stays in `system_prompt`)**: `<time_context>`, user/household
  profile text, current-user block, world model text, active task text,
  conversation summary, relevant memories (episodic search reruns every
  call), skills index.

This directly gives pydantic-ai's `anthropic_cache_instructions` a stable
boundary to place the breakpoint at (end of `instructions`, start of dynamic
`system_prompt` suffix) without us tracking the boundary by hand — which
answers the "must be easily refreshable when we implement more tools/skills"
requirement: new tools register into the same already-cached toolset
construction path, and new skills only touch the dynamic skills-index suffix
(already the case today — skills are file-based and loaded into the index at
agent-build time, not baked into the static instructions text).

### Order of the dynamic suffix

Within the dynamic suffix, order sections from most-stable to
most-volatile: identity block → profiles → world model → **skills index**
→ active task → relevant memories (search reruns every call, so this and
active task go last). This doesn't affect Anthropic's explicit-breakpoint
caching (which only cares about the one boundary at the end of
`instructions`), but it matters for Anthropic's *automatic* mode and for
OpenAI's automatic prefix matching, both of which extend the
cacheable-match forward from the last known-identical byte — the more
stable content is contiguous at the front of the dynamic suffix, the more
of it those automatic mechanisms can still pick up even without an
explicit breakpoint there.

### Skills index caching consideration

`get_skill_registry().skills_index_text()` (`app/agent/skills.py`) is
file-based and stable between `/reload`s. Default to keeping it in the
dynamic suffix (per the ordering above, placed before active task/memories,
not after) — moving it into the static `instructions` block is only worth
doing if measurement shows the index is large enough to matter; for a
handful of skill entries the win is likely marginal against the added
complexity of another moving piece in the static boundary.

---

## Wiring `model_settings`

`run_conversation()` (`app/agent/agent.py`) currently passes a single
provider-agnostic `ModelSettings(max_tokens=settings.max_tokens_per_run)` to
`agent.run()`. `ModelSettings` is a `TypedDict` (confirmed:
`ModelSettings.__mro__` includes `dict`), so passing extra provider-specific
keys works fine at runtime regardless of which provider actually handles the
call — but under this repo's `mypy --strict` gate, constructing a plain
`ModelSettings(...)` literal with `anthropic_cache_instructions=...` or
`openai_prompt_cache_key=...` keys will not type-check, since those keys
only exist on the narrower `AnthropicModelSettings` /
`OpenAIChatModelSettings` TypedDicts, not the base `ModelSettings`. This
needs a decided construction pattern before implementation — options:

1. Build a plain `dict[str, object]` and `cast(ModelSettings, ...)` it once,
   documented as intentional (simplest, one `# type: ignore` or `cast` site).
2. Branch on which model is active and construct the provider-specific
   TypedDict for that branch (type-safe, but duplicates the common keys like
   `max_tokens` in both branches, and needs to know the provider ahead of
   the call — awkward given `agent_run()`'s per-call `model=` override).

Leaning toward (1) for the first version — the settings dict is inherently
cross-provider by design here (see "Provider-agnostic implication" above),
so a single cast site is more honest than pretending it's provider-specific
at the type level.

Illustrative, not final:

```python
model_settings = cast(
    ModelSettings,
    {
        "max_tokens": settings.max_tokens_per_run,
        "anthropic_cache_instructions": "5m",
        "anthropic_cache_tool_definitions": "5m",
        "openai_prompt_cache_options": {"mode": "explicit"},  # deliberately off — see decision gate above
    },
)
```

Since `agent_run()` already fails over between `model_chain` candidates
(primary → fallback, see `app/agent/llm_router.py` /
`app/agent/runner.py`), these settings are passed unconditionally on every
call — the provider actually handling the call (primary or fallback) picks
up whichever subset of keys it understands.

---

## Instrumentation

Extend `_write_run_log()` (`app/agent/runner.py`) to also persist
`usage.cache_write_tokens` / `usage.cache_read_tokens` into
`AgentRunLog.tokens_used` (currently `{"input": ..., "output": ...}` →
`{"input": ..., "output": ..., "cache_write": ..., "cache_read": ...}`), and
the model/provider actually used for the call (already available as
`model_name` in `runner.py`, but currently only stored as
`AgentRunLog.model_used` — cross-reference the two, don't duplicate).
Without this, there's no way to confirm the change is working or to catch a
future edit that accidentally breaks the cacheable prefix (e.g. someone adds
a per-call variable back into the static `instructions` text).

**Avoid a single "cache-hit rate" number** — it's not precise enough to be
useful, and Anthropic/OpenAI account for cache writes and reads at different
prices (a cache write is typically *more* expensive than a normal input
token; a cache read is cheaper). `/admin/stats` (`app/control/api.py`)
should report, broken down **per provider/model**:

- total `cache_read_tokens` and `cache_write_tokens` (raw, not a ratio)
- a **weighted cached-input share**: `cache_read_tokens divided by total
  input (input_tokens plus cache_read_tokens plus cache_write_tokens)` over
  a window — this is the number that actually correlates with cost savings,
  since it accounts for the relative size of cached vs. non-cached input
  rather than just counting how many runs got any cache hit at all
- run count, for context, but not as the hit-rate denominator itself

**Anthropic's `input_tokens` is the uncached remainder only** (see the
mechanics section above): once caching lands, raw `input_tokens` will drop
sharply without total prompt size having changed. Every place `/admin/stats`
or the dashboard currently sums `input` as "total input tokens" must switch
to the three-field sum, or the graphs will show a phantom improvement and
hide regressions later.

Also add **latency instrumentation split by cache state** — a run that hit
the cache (`cache_read_tokens > 0`) vs. a cold/cache-write run vs. a
tool-heavy run (many tool round-trips inflate latency independently of
caching) should be reported separately. An overall average latency would
mask the actual improvement, especially early on when most calls are still
cache-write (first hit) rather than cache-read.

---

## Implementation plan (steps, files, tests)

Each step is independently shippable; run
`uv run ruff check app/ && uv run pytest tests/unit/ -v --tb=short` per step.

**Step 1 — split static prompt content from per-call rendering.**
Files: `prompts/persona.md`, `app/agent/prompts.py`.

- Edit `persona.md`: remove the two identity sentences (lines ~16–17) and the
  per-call variable references from the body, leaving a placeholder-free
  tone/style/behavior document. `instructions.md` is already variable-free.
- In `prompts.py`: add `load_static_instructions() -> str` (persona body +
  instructions body, `@lru_cache`d, NO `format_map`) and
  `render_identity_block(agent_name, household_name, user_name) -> str`
  producing the one-paragraph identity text that used to open persona.
- Tests (`tests/unit/test_prompts_static_split.py`): static loader returns
  byte-identical output across calls; static output contains no `{` template
  placeholders; identity block contains the passed names; combined content
  (static + identity) covers everything the old rendered persona said
  (spot-assert key sentences).

**Step 2 — restructure the agent's prompt assembly.**
Files: `app/agent/agent.py`.

- Pass `instructions=load_static_instructions()` to the `Agent(...)`
  constructor in `_make_conversation_agent()` (rebuilt on `/reload` via the
  existing `reload_agent()` path — no new invalidation plumbing).
- Reduce the `@a.system_prompt` closure to the dynamic suffix only, ordered
  most-stable → most-volatile: identity block → time_context → user profile →
  household profile → current user → world model → skills index → active
  task → conversation summary → relevant memories.
- Tests (`tests/unit/test_agent_prompt_split.py`): build the agent with two
  different `AgentDeps` (different user/time) and assert the `instructions`
  text is byte-identical while the system-prompt output differs; assert
  identity names appear in the dynamic part, not the static part.

**Step 3 — pass caching model settings.**
Files: `app/agent/agent.py` (`run_conversation()`), `app/config.py`,
`pyproject.toml` (pydantic-ai version bump to 2.21.0).

- Add a `STATIC_PROMPT_CACHE_VERSION` constant (manual bump, documented) for
  `/admin/stats` attribution (see cache-key-versioning above).
- Anthropic: `anthropic_cache_instructions="5m"`, `anthropic_cache_tool_definitions="5m"`
  (explicit TTL literal rather than `True` so the choice is visible).
- OpenAI: `openai_prompt_cache_options={"mode": "explicit"}`, no
  `CachePoint` placed anywhere, no `openai_prompt_cache_key` — deliberately
  disables GPT-5.6 implicit-mode caching per the resolved decision gate
  above (pydantic-ai has no system-level breakpoint hook, so leaving
  implicit mode on would pay the write premium with no reads).
- Add a `feature_prompt_caching: bool = True` flag in `Settings` so the whole
  thing (Anthropic caching + the OpenAI explicit-off setting) can be
  reverted to the old plain `ModelSettings(max_tokens=...)` from `.env`.
- Tests (`tests/unit/test_model_settings_caching.py`): settings dict contains
  the expected Anthropic keys and the OpenAI `mode: "explicit"` key when the
  flag is on; omits all caching keys when off; mypy clean.

**Step 4 — instrumentation.**
Files: `app/agent/runner.py`, `app/control/api.py`, `app/control/dashboard.html`.

- Read `usage.cache_read_tokens` / `usage.cache_write_tokens` in the
  usage-extraction block; thread through `RunOutcome`, `run.complete` event,
  and `_write_run_log()` (`tokens_used` JSON gains `cache_read`/`cache_write`
  keys — no schema migration needed, it's a JSON string column).
- `/admin/stats`: switch total-input to the three-field sum; add
  cache_read/cache_write totals and the weighted cached-input share per
  model; split average latency by cache state (read-hit / write / no-cache).
- Tests: extend `tests/unit/test_agent_runner_failover.py` fixtures' fake
  usage with cache fields and assert they land in `RunOutcome` and the run
  log payload; a stats-aggregation unit test over seeded `AgentRunLog` rows.

**Step 5 — end-to-end verification (manual, dev runtime).**
Run `APP_ENV=development uv run python -m app` and drive real runs — a
two-call check only proves the trivial case. At minimum:

- same user, two messages seconds apart → expect `cache_read_tokens > 0`
  on the second call.
- a static-content change (edit `instructions.md`, hit `/reload`) →
  expect the *next* call to be a cache write (miss), not a read, and the
  response content to reflect the edit.
- a different user/household in the same run (Anthropic) → expect the
  static-prefix cache to still hit while the identity block and profile
  content are still correctly household/user-specific — this is the check
  that catches a caching bug that leaks stale identity/profile text across
  users.
- a change to only the dynamic context (time, active task, new memory)
  with the static content unchanged → expect a cache read on the static
  prefix even though the overall prompt differs.
- a toolset/feature-flag change (e.g. toggle `FEATURE_WINE`) → expect a
  cache miss on the tool-definitions breakpoint specifically, confirming
  the tool-schema cache isn't stale against the registered toolset.
- one run with Anthropic primary/fallback active, to confirm the explicit
  breakpoints actually hit (`cache_read_tokens > 0` on the second call, per
  the first bullet above).
- **GPT-5.6 check: confirm caching is actually off.** Inspect the raw
  response/usage on a GPT-5.6 call — expect `cache_write_tokens` (or
  `cached_tokens`) to be zero or absent, confirming
  `openai_prompt_cache_options={"mode": "explicit"}` with no breakpoints
  really does suppress the implicit-mode write premium. This is the
  opposite check from Anthropic's — here we're verifying the *absence* of
  cache activity, not its presence.

Each step is independently shippable and testable — no need to land this as
one large change.
