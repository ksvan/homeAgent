# Agent Skills Design

## Purpose

Skills extend the agent with domain-specific knowledge, API workflows, and
helper scripts — without requiring new MCP tools or code changes. A skill
encodes how to solve a class of problems (e.g. Norwegian weather, road traffic)
using a specific data source.

Goals:

- File-based: drop a folder into `app/skills/` and it is picked up on restart.
- Lazy: skill content is not injected into every system prompt. The agent sees
  a compact index, then fetches full guidance on demand.
- Scriptable: each skill can include Python helper scripts, run via the
  existing `run_python_script` tool.
- Observable: skill lookups are logged and appear in the admin dashboard.

Non-goals:

- No CRUD management in the admin UI.
- Not a plugin system for tools (that is MCP's job).

---

## Folder Structure

```text
app/skills/
  <skill-name>/
    SKILL.md              ← required; frontmatter + workflow guidance
    agents/
      agent.yaml          ← optional; display_name, short_description, default_prompt
    references/
      *.md                ← optional; API reference docs, read on demand
    scripts/
      *.py                ← optional; helper scripts
```

### SKILL.md

YAML frontmatter with at minimum `name` and `description`:

```yaml
---
name: metno-norway-weather
description: Fetch and interpret Norwegian weather data from MET Norway's open APIs.
---
```

The body is the full workflow guidance the agent receives when it calls
`get_skill(name)`.

### agents/agent.yaml

Model-agnostic interface metadata:

```yaml
interface:
  display_name: "MET.no Norway Weather"
  short_description: "Use MET Norway APIs for weather, alerts, tides"
  default_prompt: "Use $metno-norway-weather to fetch and interpret forecasts..."
```

The `default_prompt` is shown in the system-prompt skills index as the
invocation hint. If `agent.yaml` is absent, the index falls back to the
`description` from SKILL.md.

---

## Runtime Integration

### SkillRegistry (`app/agent/skills.py`)

Scans `app/skills/` at startup. Parses SKILL.md frontmatter and `agent.yaml`
for each subdirectory. Exposes:

- `list()` → all loaded skills
- `get_content(name)` → full SKILL.md text
- `skills_index_text()` → compact `## Available Skills` block for the system prompt

Singleton: `get_skill_registry()`. Cleared and reloaded on admin `/reload`.

Config: `skills_dir` setting (default: `app/skills`, relative to repo root).

### System Prompt

The skills index is always injected at the end of the system prompt:

```text
## Available Skills

- **metno-norway-weather** — Use MET Norway APIs for weather, alerts, tides
  Invoke: "Use $metno-norway-weather to fetch and interpret forecasts..."
- **vegvesen-datex** — Bruk Vegvesen DATEX for trafikkdata i Norge
  Invoke: "Use $vegvesen-datex to fetch and interpret Norwegian traffic..."
```

### Agent Tools (`app/agent/tools/skills.py`)

- `get_skill(name)` — returns full SKILL.md content; logs the lookup
- `list_skills()` — lists all skills with name and description

### Admin API

`GET /admin/skills` — returns all skills with name, display_name, description,
short_description, has_scripts, has_references.

### Admin Dashboard

The Control Loop tab shows a Skills card in the status strip (count) and a
Skills panel listing each skill's name, invoke token, and short description.

---

## Adding a New Skill

1. Create `app/skills/<name>/SKILL.md` with frontmatter and workflow guidance.
2. Optionally add `agents/agent.yaml`, `references/`, and `scripts/`.
3. Restart the agent (or hit `/reload` in the admin UI).
4. The skill appears in the agent's system prompt index and in the dashboard.

---

## Implemented Skills

| Name | Domain | Scripts | References |
| ---- | ------ | ------- | ---------- |
| `metno-norway-weather` | Norwegian weather (MET.no) | `metno_fetch.py` | `metno-api-reference.md` |
| `vegvesen-datex` | Norwegian road traffic (Vegvesen DATEX II) | `datex_fetch.py` | `datex31-reference.md` |
