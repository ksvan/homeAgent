# Wine Cellar Agent Tool Design

## Purpose

Make HomeAgent aware of the wine currently available in the household cellar so it can answer questions like:

- "What should we drink with lamb tonight?"
- "Do we have a good white for shellfish?"
- "Which bottles should we drink soon?"
- "Find a red under 300 NOK from Italy."

The agent should stay fast for normal chat. Wine inventory should not be injected into every prompt. The model should call a wine-cellar tool only when the question needs current inventory.

## Current Source

The cellar is tracked in an Excel workbook in a Microsoft 365 / Office 365 tenant controlled by the household.

Current columns, in Norwegian:

```text
Hylle
Kategori
Land
Vinprodusent
Vin
Årgang
Slutt drikkevindu
Score
Pris innkjøp
Distrikt
Notat
Drukket
```

The volume is small, roughly 100 bottles at a time. That means we can afford simple snapshot parsing and in-memory/domain filtering. We do not need a search index or vector database for V1.

## Recommendation

Use Microsoft Graph to download the workbook file with app-only access, parse the Excel table locally, normalize it into a small wine inventory snapshot, and expose read-only wine tools to the agent.

This is better than using Microsoft Graph's Excel workbook table APIs as the primary path because the Graph Excel workbook/table APIs are delegated-only for table operations. Microsoft documents `Application: Not supported` for workbook table listing, while file download supports application permissions. For a headless household agent, app-only file access is operationally cleaner than storing a delegated user refresh token.

References:

- Microsoft Graph workbook table list API: application permission is not supported.
  https://learn.microsoft.com/en-us/graph/api/table-list?view=graph-rest-1.0
- Microsoft Graph driveItem content download supports application permissions and returns the file content via redirect/download URL.
  https://learn.microsoft.com/en-us/graph/api/driveitem-get-content?view=graph-rest-1.0
- Microsoft Graph selected permissions can restrict an app to selected SharePoint/OneDrive resources.
  https://learn.microsoft.com/en-us/graph/permissions-selected-overview
- Microsoft Graph Excel API best practices recommend workbook sessions for multiple Excel API calls, but this is mainly relevant if we later use delegated workbook APIs.
  https://learn.microsoft.com/en-us/graph/workbook-best-practice

## Design Principles

- **Reactive, not prompt-loaded**: inventory is fetched through a tool when needed.
- **Read-only V1**: answer from the source of truth, but do not write back to Excel yet.
- **Least privilege**: the app should only read the one workbook or containing site/folder.
- **Source remains human-friendly**: Excel stays the owner-facing edit surface.
- **Local normalized snapshot**: parse once, then query a clean internal shape.
- **Graceful degradation**: stale cached data is better than no answer if Graph is temporarily unavailable, but the agent must disclose staleness.
- **No separate wine expert model**: the LLM already knows wine and pairing; the tool provides available inventory, not tasting theory.

## Access Options

### Option A: App-only Graph file download, parse locally

This is the recommended V1.

Flow:

1. Register an Entra ID app for HomeAgent.
2. Grant least-privilege access to the workbook or containing site.
3. HomeAgent obtains a client-credentials token.
4. HomeAgent downloads the `.xlsx` file through Graph.
5. HomeAgent parses the table locally with `openpyxl`.
6. HomeAgent caches normalized bottle rows keyed by Graph `eTag`.
7. Agent tools query the normalized snapshot.

Pros:

- Works unattended.
- Does not need a service user's refresh token.
- Keeps Excel as the source of truth.
- Only about 100 rows, so local parsing is cheap.
- Avoids the delegated-only limitation of workbook table APIs.

Cons:

- Requires adding an `.xlsx` parser dependency.
- Read-only unless we later implement whole-file upload or a separate write path.
- The app sees the file contents, so secrets/tenant access must be handled carefully.

Permissions:

- Best: `Files.SelectedOperations.Selected` or selected-resource access to the single workbook, if practical in your tenant setup.
- Acceptable: `Sites.Selected` for the SharePoint site containing the workbook.
- Avoid if possible: tenant-wide `Files.Read.All` or `Sites.Read.All`.

Authentication:

- V1 will use app-only client credentials with an Entra ID app client ID and
  client secret.
- Store the client secret only in `.env`; `.env` is ignored and must not be
  committed.
- Only the Graph client should read the secret. It must never appear in tool
  output, admin responses, logs, run logs, or prompt context.
- Certificate credentials can remain a later hardening option, but they are not
  part of V1.

Required V1 config:

```env
WINE_GRAPH_TENANT_ID=...
WINE_GRAPH_CLIENT_ID=...
WINE_GRAPH_CLIENT_SECRET=...
WINE_GRAPH_DRIVE_ID=...
WINE_GRAPH_ITEM_ID=...
WINE_EXCEL_TABLE_NAME=...
```

Optional config:

```env
WINE_WORKSHEET_NAME=...
WINE_CACHE_TTL_SECONDS=21600
WINE_REFRESH_CRON=0 6 * * *
WINE_SEARCH_DEFAULT_LIMIT=20
```

`WINE_GRAPH_ITEM_ID` is preferred over path-based lookup because it is stable
when the workbook is renamed or moved inside the same drive. Path lookup can be
added later as an admin convenience.

### Option B: Delegated Graph Excel APIs

Use Graph workbook/table APIs with a signed-in account.

Pros:

- Can query tables directly.
- Better if later we want row-level Excel writes.
- Can use workbook sessions for multiple calls.

Cons:

- Requires delegated auth and token refresh management.
- The bot becomes coupled to a user/service account.
- Token expiry/consent failures are operationally messier for a home daemon.
- Less aligned with unattended runtime.

This is not recommended for V1 unless app-only selected file access is blocked.

### Option C: Move source to a SharePoint List

Make the cellar a structured list instead of an Excel table.

Pros:

- Better API semantics for rows/items.
- Easier app-only reads and later writes.
- Natural schema and filtering.
- Better audit/history than Excel row mutation.

Cons:

- Changes the household editing workflow.
- Less familiar than an Excel table.
- Migration effort.

This is a strong future option if the cellar evolves into a first-class inventory system with edits, purchase tracking, consumed bottles, photos, or multiple users updating from phones.

### Option D: Manual import / CSV upload fallback

Admin uploads or places an exported CSV/XLSX in `data/`.

Pros:

- Very simple.
- No Microsoft 365 auth initially.
- Good emergency fallback.

Cons:

- Manual drift.
- User explicitly wants to avoid this if possible.

Keep as fallback, not primary.

## Proposed Architecture

```text
Office 365 Excel workbook
        |
        | Microsoft Graph driveItem content
        v
Wine Source Client
        |
        | .xlsx bytes + eTag
        v
Wine Snapshot Parser
        |
        | normalized bottle rows
        v
Wine Inventory Cache (cache.db)
        |
        | queried by built-in agent tools
        v
Conversation Agent
```

Placement:

- `app/wine/graph_client.py`
- `app/wine/models.py`
- `app/wine/parser.py`
- `app/wine/repository.py`
- `app/wine/sync.py`
- `app/agent/tools/wine.py`
- optional admin visibility under `/admin/wine` later

Storage:

- Normalized snapshot stored in `cache.db` — the correct home for operational
  runtime state derived from an external source (same pattern as `devicesnapshot`).
- `users.db` is for canonical household entities; wine bottles are not canonical
  household entities.

```text
cache.db tables:
  winebottle     — one row per bottle in current snapshot
  winesyncmeta   — single-row: etag, last_sync_at, last_attempt_at,
                   row_count, parse_warnings (JSON), sync_error
```

Why not Tools MCP:

- The existing Tools MCP SharePoint functions are generic document readers and currently assume anonymous/guest access.
- Wine inventory is household domain state, not a generic scrape.
- Credentials, source health, caching, and admin observability fit better inside the main app.

## Normalized Data Shape

The parser maps Norwegian source columns into stable internal fields:

```python
WineBottle(
    id: str,                            # stable hash of source_row + source_etag
    shelf: str | None,                  # Hylle
    category: str | None,               # Kategori
    country: str | None,                # Land
    producer: str | None,               # Vinprodusent
    name: str,                          # Vin
    vintage: int | None,                # Årgang
    drink_window_end: date | None,      # Slutt drikkevindu
    score: float | None,                # Score
    purchase_price_nok: float | None,   # Pris innkjøp
    region: str | None,                 # Distrikt
    note: str | None,                   # Notat
    consumed: bool,                     # Drukket
    source_row: int,                    # Excel row index — preserved for future write-back
    source_hash: str,
)
```

Derived fields (computed, not stored):

- `display_name`: producer + wine + vintage.
- `available`: `not consumed`.
- `drink_status`: `drink_now`, `hold`, `past_window`, `unknown`.

The LLM reasons about style, pairing suitability, and quality from the raw structured
fields directly. No Python-level style inference is performed by the tool.

Column handling:

- Match known Norwegian column names exactly first.
- Also support aliases so the sheet can evolve:
  - `Vinprodusent`: `Produsent`, `Producer`
  - `Årgang`: `Aargang`, `Vintage`
  - `Slutt drikkevindu`: `Drikkevindu slutt`, `Drink by`
  - `Drukket`: `Consumed`, `Drunk`
- Unknown columns are ignored but logged at WARNING.
- Missing required fields produce a sync warning, not a crash.

`Drukket` parsing:

- `"Ja"` → `consumed = True`
- `"Nei"` or empty/blank → `consumed = False` (still available)

Required V1 fields:

- `Vin`

Strongly preferred:

- `Kategori`
- `Land`
- `Vinprodusent`
- `Årgang`
- `Drukket`

## Sync and Refresh

### `sync_wine_cellar(force: bool = False) -> WineSyncResult`

Single shared entry point for all sync triggers. Lives in `app/wine/sync.py`.

Called from four places:

1. **Reactive** — `search_wine_cellar` / `get_wine_cellar_summary` tools when cache
   is stale (TTL exceeded or no snapshot exists)
2. **Scheduled** — APScheduler daily `CronTrigger` job (default `0 6 * * *` in
   household timezone, configurable via `WINE_REFRESH_CRON`)
3. **Agent tool** — `refresh_wine_cellar` (no inputs, always `force=True`); triggered
   when the user says "refresh the wine list" in chat
4. **Admin** — future `/admin/wine/refresh` endpoint

Behavior:

1. Fetch current eTag from Graph (lightweight metadata call).
2. If `force=False` and eTag matches and cache is within TTL → return cached result.
3. Download `.xlsx` bytes via Graph driveItem content endpoint.
4. Parse with `openpyxl`.
5. Atomically replace `winebottle` rows and update `winesyncmeta`.
6. Emit control events (see below).

Concurrency: a module-level `asyncio.Lock` in `sync.py` ensures that simultaneous
calls (e.g. two users asking wine questions at the same moment) wait for the
in-flight sync to complete rather than triggering duplicate Graph downloads.

### Control events emitted

| Event | When |
| --- | --- |
| `wine.sync_started` | Download begins |
| `wine.sync_completed` | Snapshot updated successfully |
| `wine.sync_failed` | Graph error or parse error with no usable result |
| `wine.cache_used_stale` | Returned cached data because Graph was unavailable |
| `wine.parse_warning` | Unknown columns or missing preferred fields |

### Failure behavior

- Graph unavailable but cache exists → return cached snapshot with `stale=True`
  and last successful sync time; agent discloses staleness to user.
- No cache and Graph unavailable → return clear tool error:
  `"Wine cellar source unavailable; no cached inventory exists."`

### Size and resource limits

- Max workbook download size: 10 MB.
- Max parsed rows: 1,000.
- Reject files that are not `.xlsx`.

## Tool Surface

### `search_wine_cellar`

Primary reactive tool.

Inputs:

```python
name_search: str | None = None        # substring match on name + producer only
food: str | None = None
occasion: str | None = None
max_price_nok: float | None = None
category: str | None = None
country: str | None = None
region: str | None = None             # substring match on Distrikt
include_consumed: bool = False
limit: int | None = None              # defaults to WINE_SEARCH_DEFAULT_LIMIT (20)
```

`name_search` is a narrow, explicit substring match on `name` and `producer` fields
only. The LLM decomposes natural language intent into the appropriate structured
fields (`food`, `category`, `country`, `region`, etc.) rather than passing free-form
text. This keeps tool behavior deterministic and predictable.

The tool is designed for iterative querying. The LLM may call it multiple times,
narrowing by `category`, `country`, or `region` to find the best available match,
or broadening if an initial call returns too few results. Filters can be combined
freely — e.g. `food="lamb", country="Italy", region="Barolo"`.

Ranking varies by query intent:

- **Food or occasion query** (`food` or `occasion` provided): food-category match
  first, then source score, then drink-window urgency. Surfaces the best available
  pairing candidates regardless of urgency.
- **Availability / drink-soon query** (no food context, no filters): drink-window
  urgency first, then score. Returns what should be drunk soonest.
- **Name or filter query** (`name_search`, `country`, `category`, `region`): source
  score first, then drink-window urgency.

Returns: candidate available bottles (structured with all fields the LLM needs to
reason about: vintage, region, score, note, drink_status), source freshness, and any
sync warnings. The tool retrieves and ranks; the LLM selects the best match and
explains the pairing from its own wine knowledge.

### `get_wine_cellar_summary`

Useful for broad questions like "What wines do we have?" or "What should we drink
soon?"

Returns:

- total available bottles
- categories/counts
- countries/counts
- drink-window warnings
- last sync time

### `get_wine_bottle_detail`

Optional V1. Useful when the user asks about a specific bottle from a search result.

Inputs:

```python
bottle_id: str
```

Returns the full normalized row.

### `refresh_wine_cellar`

No inputs. Calls `sync_wine_cellar(force=True)`. Returns sync result summary
(row count, new eTag, duration, any parse warnings).

Allows the user to say "refresh the wine list" or "sync the cellar" in chat to
trigger an immediate update.

### Later: `mark_wine_consumed`

Out of scope for V1.

Since Option A is the access path (file download + local parse), the Phase 3
write-back path would be: download current file → modify the `Drukket` cell for
the target row → upload the full modified file back via Graph. `source_row: int`
is preserved in the `WineBottle` model precisely to identify which row to patch.
No design decision is needed now; the data model is already compatible.

## Agent Instructions

Add compact instructions to `prompts/instructions.md`, not a large wine guide:

- Use `search_wine_cellar` when the user asks for wine pairing, wine availability,
  cellar contents, drink-window timing, or bottle recommendations from inventory.
- Do not assert "we have X" unless a wine tool was called in this conversation with
  fresh results.
- For general wine education with no inventory relevance, answer directly.
- For pairing: call `search_wine_cellar` with `food` set, then reason about the
  returned candidates using your own wine knowledge to pick the best available bottle.
  You may call the tool again with narrower filters (`country`, `region`, `category`)
  if the first result set doesn't contain a strong match, or broader filters if it
  returns too few candidates.
- If no good match exists, say so and suggest the closest available alternative.
- If the cellar snapshot is stale, disclose it briefly.
- Use `refresh_wine_cellar` if the user explicitly asks to sync or refresh the cellar.

## Query and Ranking Behavior

### Filtering

1. Refresh snapshot if cache is stale or source eTag changed.
2. Exclude consumed bottles by default (`include_consumed=False`).
3. Apply explicit filters: `category`, `country`, `region`, `max_price_nok`,
   `name_search`. Filters combine with AND semantics.
4. Return up to `limit` candidates (default: `WINE_SEARCH_DEFAULT_LIMIT`, 20).

### Ranking by query intent

**Food or occasion query** (`food` or `occasion` provided):

1. Food-category match (light heuristics; see below)
2. Source score
3. Drink-window urgency

Rationale: surfaces the best available pairing option first, not the most urgent
one. The LLM then selects from the returned set using its own wine knowledge.

**Availability / drink-soon query** (no food, no explicit filters):

1. Drink-window urgency
2. Source score

**Name or attribute query** (`name_search`, `country`, `category`, `region`):

1. Source score
2. Drink-window urgency

### Iterative querying

The tool is designed to be called multiple times within a single conversation turn.
The LLM is expected to:

- Start with `food` and minimal filters to get a broad candidate set.
- Narrow with `country` or `region` if the user has a preference or hypothesis
  (e.g. "I'm thinking something from Burgundy").
- Broaden by removing filters if the first call returns too few results.
- Call again with `include_consumed=True` only if explicitly needed (e.g. inventory
  audit).

### Food pairing heuristics

Used only for ranking; deliberately light. The LLM refines from raw structured data.

- shellfish/fish: white, sparkling, Champagne, Chablis, Riesling, Sauvignon Blanc
- lamb/beef/game: red, Bordeaux, Rioja, Barolo/Barbaresco, Rhône, Syrah, Cabernet, Nebbiolo
- pork/chicken: Chardonnay, Pinot Noir, lighter reds
- spicy Asian: Riesling, Gewürztraminer, off-dry whites, sparkling
- dessert: sweet wine if present

## Security

Access:

- Use a dedicated Entra ID app.
- Prefer selected file/site permission over tenant-wide access.
- Prefer read-only access for V1.
- Store credentials in `.env`, never in prompts, logs, or admin responses.

Network:

- Call only Microsoft Graph endpoints.
- Do not follow arbitrary source URLs from user input.
- The workbook target must be configured by admin, not passed by the agent.

Logging:

- Do not log full workbook content.
- Log row counts, source IDs, eTags, sync status, and parse warnings.
- Avoid logging purchase notes if they may contain private comments.
- `WINE_GRAPH_CLIENT_SECRET` must never appear in any log line, tool output, or
  prompt context.

Agent exposure:

- Tool results can include bottle notes because that is useful context, but keep
  output bounded.
- Do not put the full cellar in every prompt.

## Admin and Operations

V1 admin visibility is minimal:

- `/status` should include wine source health once implemented.
- Admin stream shows sync success/failure via control events above.
- A future admin panel can show: last sync, row count, source eTag, parse warnings,
  sample normalized rows.

Useful slash/admin commands later:

- `/wine refresh`
- `/wine status`

## Implementation Phases

### Phase 1: Read-only inventory tool

1. `app/wine/models.py` — `WineBottle`, `WineSyncResult`
2. `app/wine/graph_client.py` — Graph auth + eTag metadata + file download
3. `app/wine/parser.py` — xlsx → `list[WineBottle]` with Norwegian column aliases
4. Alembic migration — `winebottle` + `winesyncmeta` tables in `cache.db`
5. `app/wine/repository.py` — snapshot read/write
6. `app/wine/sync.py` — `sync_wine_cellar()` with lock + control events
7. `app/agent/tools/wine.py` — four tools: `search_wine_cellar`,
   `get_wine_cellar_summary`, `get_wine_bottle_detail`, `refresh_wine_cellar`
8. `app/config.py` — wine settings group + `FEATURE_WINE`
9. `app/agent/agent.py` — register wine tools (gated on `FEATURE_WINE`)
10. `app/scheduler/jobs.py` — daily `CronTrigger` wine refresh job
11. `app/api/server.py` — start wine refresh job in lifespan
12. `prompts/instructions.md` — compact wine tool usage guidance
13. `.env.example` — wine config block
14. `tests/unit/wine/` — parser tests (fixture xlsx), repository tests, tool tests
    (mocked Graph)

### Phase 2: Better matching and admin visibility

- Drink-window status improvements.
- Source health in `/status`.
- Control events in admin stream.
- More pairing heuristics and column aliases.
- Optional `/admin/wine` panel.

### Phase 3: Consumption / write workflow

Only after V1 is stable. See `mark_wine_consumed` section above for write-back path.
Defer until actual usage shows chat-based consumption tracking is needed.

## Open Questions

1. Is the workbook stored in SharePoint or a user's OneDrive?
2. Can the tenant grant selected application access to the single workbook or site?
3. Is the data formatted as an actual Excel Table, or just a range with headers?
4. Should duplicate rows represent multiple bottles of the same wine, or should there
   be a quantity column later?
5. Should `Pris innkjøp` be exposed in normal user-facing answers, or only used
   for filtering?
6. Should all household members be allowed to ask cellar questions?
7. Is wine cellar data sensitive enough to require admin-only access?

## Current Judgement

Build this as a read-only, reactive inventory grounding tool backed by a cached
normalized snapshot from the Excel workbook. Do not preload inventory into normal
context, do not make the LLM manage the source file, and do not write back to Excel
in V1.

The most practical source-access path is app-only Microsoft Graph download of the
workbook, constrained by selected file/site permissions where possible. Local parsing
is simpler and more robust for this use case than delegated Excel table APIs. The
snapshot lives in `cache.db` alongside other operational runtime state.
