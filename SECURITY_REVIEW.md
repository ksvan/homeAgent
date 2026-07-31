# HomeAgent Security Review

**Date:** 2026-07-02
**Scope:** `app/` (FastAPI runtime, webhooks, Telegram channel, admin API, policy gate) and `services/` (tools-mcp, prometheus-mcp). Review of source only; no changes made.

> A previous `security_best_practices_report.md` exists and covered admin-port exposure. Several of its items have since been partially addressed in `docker-compose.yml` (admin port now binds `127.0.0.1` by default). This report is a fresh pass and focuses on the tool-execution surface, webhook trust, and access control. Overlap is noted where relevant.

---

## Summary

| # | Severity | Issue |
|---|----------|-------|
| 1 | High | tools-mcp exposes arbitrary code execution with no authentication (network-isolation-only trust model) |
| 2 | High | SSRF filter is bypassed by HTTP redirects in the SharePoint tools |
| 3 | Medium | Homey and AgentMail webhooks fail open (skip signature check) when no secret is configured |
| 4 | Medium | Admin API is fully open when `APP_SECRET_KEY` is unset; config default binds admin to `0.0.0.0` |
| 5 | Low | SSRF check is subject to TOCTOU / DNS-rebinding |
| 6 | Low | Policy gate fails open for unmatched `get_/list_/search_`-prefixed tool names |
| 7 | Low | Admin `/openapi.json` still served; admin landing page unauthenticated |
| 8 | Info | Secrets forwarded into subprocess env are readable by any executed script |

---

## High

### 1. tools-mcp allows arbitrary code execution with no authentication

`services/tools-mcp/app/mcp_server.py` registers `run_python_script`, which writes attacker-supplied `code` to `main.py` and runs it with `python3`. This is **arbitrary code execution by design** — the bash allowlist (`app/shell.py` `ALWAYS_BLOCKED` / `DEFAULT_ALLOWED`) and the SSRF guard (`_is_ssrf_blocked`) protect the `run_bash_command` and scrape/SharePoint tools, but a Python script bypasses all of them (it can open raw sockets to internal services, read/write the workspace, spawn processes, etc.).

The MCP server itself has **no authentication**: `main.py` starts FastMCP on `mcp_host` (default `0.0.0.0`, port `9001`) with no token or client check. The entire security boundary is Docker network isolation — the `tools` service does not publish a port in `docker-compose.yml`, so it is only reachable on the internal compose network.

**Why it matters:** the boundary is a single misconfiguration away from full RCE. If the port is ever published (e.g. for debugging, a `ports:` entry, or running the service outside compose with the `0.0.0.0` default), or if any other container/host on the same network is compromised, an attacker gets code execution and can read every secret passed via `tools_passthrough_env` (see #8). There is no defense-in-depth behind the network layer.

**Recommendations:**
- Add a shared-secret / bearer check to the tools-mcp endpoint so network reachability alone is not sufficient.
- Change the `mcp_host` default from `0.0.0.0` to `127.0.0.1` and require an explicit opt-in for non-loopback binding.
- Document the "network isolation is the only boundary" assumption prominently, and ensure `feature_python`/`feature_bash` are off unless needed.

### 2. SSRF filter is bypassed by redirects in the SharePoint tools

`_is_ssrf_blocked()` (`services/tools-mcp/app/mcp_server.py:27`) resolves the hostname of the **supplied** URL and rejects private/loopback/link-local/multicast targets. It is called once, on the input URL.

But `sharepoint_list_files` and `sharepoint_download_file` create their HTTP client with `follow_redirects=True` (lines ~419 and ~512). A public URL that passes the check can then `302`-redirect to an internal address (`http://169.254.169.254/…`, `http://192.168.x.x/…`, another container), and httpx will follow it — the redirect target is never re-validated.

Note the `scrape_web_page` tool does this correctly: it uses `follow_redirects=False`. The SharePoint tools are the gap. `sharepoint` is enabled in `docker-compose.yml` (`FEATURE_SHAREPOINT: "true"`).

**Recommendations:**
- Disable automatic redirects and re-run `_is_ssrf_blocked()` on each hop (manual redirect loop), or use a transport that validates the resolved IP of every connection.
- Apply the same treatment anywhere `follow_redirects=True` is combined with a user/agent-controlled URL.

---

## Medium

### 3. Homey and AgentMail webhooks fail open when no secret is set

In `app/api/webhooks.py`:
- `/webhook/homey/event` only verifies `x-homey-secret` **if** `settings.homey_webhook_secret` is set (line ~81). With no secret configured, any unauthenticated caller who can reach the webhook port can inject synthetic device-state / flow-trigger events into the event bus, which drive event rules and agent behavior.
- `/webhook/agentmail` similarly skips Svix signature verification when `agentmail_webhook_secret` is empty (line ~159).

By contrast, the Telegram path is enforced: `Settings._require_webhook_secret` rejects startup if a bot token is set without a webhook secret, and the handler uses `secrets.compare_digest`. The webhook port (`8080`) is fronted by a Cloudflare tunnel, so these endpoints are internet-reachable.

**Recommendation:** fail closed — reject inbound Homey/AgentMail webhooks when the corresponding secret is not configured (mirror the Telegram validator), or refuse to enable the feature without a secret.

### 4. Admin API is open when `APP_SECRET_KEY` is unset; config default binds `0.0.0.0`

`app/control/auth.py` `require_admin_auth` returns early (open access) whenever `app_secret_key` is empty. There is a good production guard in `app/__main__.py` (SystemExit if `app_env == "production"` and key `< 32` chars). However:
- `Settings.admin_host` defaults to `0.0.0.0` (`app/config.py:137`). `docker-compose.yml` overrides it to `127.0.0.1`, but any run outside that compose file (bare `python -m app`, a different orchestrator, or `ADMIN_HOST=0.0.0.0` for "LAN access" as the comment invites) exposes the full admin surface — world-model writes, task cancel/resume, `run-now`, event-rule CRUD — with authentication depending entirely on the key being set in a non-production `app_env`.
- The admin data endpoints are all state-changing/PII-exposing (`/admin/memory` returns profiles and conversation summaries).

**Recommendation:** default `admin_host` to `127.0.0.1`; require `APP_SECRET_KEY` whenever `admin_host` is non-loopback regardless of `app_env`.

---

## Low

### 5. SSRF check is subject to TOCTOU / DNS rebinding

`_is_ssrf_blocked()` performs its own `getaddrinfo`, then httpx performs a **separate** DNS resolution when connecting. An attacker controlling DNS can return a public IP to the first lookup and a private IP to the second (DNS rebinding), or simply change the record in between. Pin the resolved IP and connect to it directly, or resolve once and pass the address to the client.

### 6. Policy gate fails open for read-prefixed tool names

`evaluate_policy()` (`app/policy/gate.py:94`) auto-approves any unmatched tool whose name starts with `get_`, `list_`, or `search_`. A tool (or a hallucinated/model-chosen tool name) that begins with one of these prefixes but has side effects would skip confirmation. This is a deliberate convenience default, but it trusts naming convention for a security decision. Consider an explicit read-only allowlist rather than a prefix heuristic for anything reaching side-effecting toolsets.

### 7. Admin OpenAPI schema and unauthenticated landing page

The admin app is created with `docs_url=None, redoc_url=None` but not `openapi_url=None`, so `/openapi.json` still enumerates the admin API. The `GET /admin` HTML page has no `dependencies=_auth` (only the data routes do). Both are reconnaissance aids; low impact given the page is a static shell and data routes are protected, but worth closing (`openapi_url=None`, and gate the page too). Carried over from the prior report and still present.

---

## Informational

### 8. Secrets in subprocess passthrough env

`tools_passthrough_env` (`services/tools-mcp/app/config.py`) forwards named host env vars (API keys, skill credentials) into the environment of bash/python subprocesses. Combined with #1, any executed script can read them. This is intentional for skill credentials, but note it widens the blast radius of any code-execution issue — keep the passthrough list minimal.

---

## Positive observations

- Telegram webhook enforces a secret at startup and compares it in constant time; body-size guards precede JSON parsing on all webhooks.
- The bash runner is genuinely hardened: `shell=False`, hardcoded `ALWAYS_BLOCKED` (shells, `sudo`, network tools, `rm`), allowlist, workspace-confined `cwd` with traversal/symlink resolution, clean minimal env, process-group kill on timeout, and output truncation.
- Flight webhook tokens are looked up by SHA-256 hash and providers verify signatures; per-watch tokens are high-entropy.
- Admin auth uses `secrets.compare_digest`; production startup fails fast without a strong key.
- Prometheus MCP is read-only with range/step/cardinality guardrails; secrets are git-ignored (`.env`, `*.db`, `data/`, `secrets/`) and none are tracked.

---

*Severity reflects likelihood × impact in the expected single-household, Docker-Compose + Cloudflare-tunnel deployment. Items #1 and #2 are the priorities.*
