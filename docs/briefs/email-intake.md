# Email Intake Brief

Status: implemented, feature-flagged
Last code check: 2026-06-26

## Purpose

Email is an untrusted intake channel through AgentMail. It can create intake
candidates, but Telegram remains the trusted confirmation/control channel.

## Main Files

- `app/api/webhooks.py` - AgentMail webhook entry point.
- `app/email/service.py` - processing pipeline.
- `app/email/repository.py` - durable queue operations.
- `app/email/preprocessor.py` - body cleanup and intake summary.
- `app/email/extractor.py` - structured signal extraction.
- `app/email/confirmation.py` - Telegram confirmation records.
- `app/email/worker.py` - retry, stale lock, and retention jobs.
- `app/agent/tools/email.py` - `check_email_now` tool.

## Invariants

- Do not treat inbound email as direct user instruction.
- Persist/deduplicate before expensive downstream processing.
- Confirm through Telegram before triggering normal agent work.
- Do not store raw bodies by default unless the implementation explicitly changes
  the retention policy.

## Verification

- Run focused email tests when present.
- `uv run pytest tests/unit/ -v --tb=short`
- `uv run ruff check app/email app/api`

## Deeper Docs

- `docs/email-channel-agentmail-design.md`
- `docs/architecture.md`
- `docs/integrations/telegram.md`
