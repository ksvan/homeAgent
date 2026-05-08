"""
Compact email preprocessor for intake summaries.

Converts raw email text into a bounded, clean intake summary suitable for
the Telegram confirmation prompt and eventual agent_run input.
"""
from __future__ import annotations

import re

from app.email.agentmail_client import AgentMailMessage

# Separators used in Outlook/Exchange forwarded messages
_FORWARD_SEPARATORS = re.compile(
    r"-{5,}\s*(Forwarded message|Original message|Begin forwarded message)"
    r"|-{20,}"
    r"|_{20,}"
    r"|From:.*\nSent:.*\nTo:",
    re.IGNORECASE | re.MULTILINE,
)

_BOILERPLATE = re.compile(
    r"(^unsubscribe.*$"
    r"|^this (email|message) (was sent|is intended).*$"
    r"|^if you (received|are not the intended).*$"
    r"|^confidentiality notice.*$"
    r"|^this transmission.*$"
    r"|^disclaimer:.*$"
    r"|\[image:.*?\]"
    r"|caution:.*external email.*$)",
    re.IGNORECASE | re.MULTILINE,
)

_EXCESS_BLANK = re.compile(r"\n{3,}")
_MAX_BODY_CHARS = 2_000
_MAX_INSTRUCTION_CHARS = 500


def _clean(text: str) -> str:
    text = _BOILERPLATE.sub("", text)
    text = _EXCESS_BLANK.sub("\n\n", text)
    return text.strip()


def _split_instruction_and_body(text: str) -> tuple[str, str]:
    """Split top user instruction from forwarded/quoted body."""
    m = _FORWARD_SEPARATORS.search(text)
    if m:
        instruction = text[: m.start()].strip()
        body = text[m.start() :].strip()
    else:
        instruction = ""
        body = text.strip()
    return instruction, body


def build_intake_summary(
    msg: AgentMailMessage, max_chars: int = 8_000
) -> tuple[str, str, str]:
    """
    Build a compact intake summary from a full AgentMail message.

    Returns (instruction_text, intake_summary_text, proposed_action_json).
    instruction_text is the user's top instruction (before any forwarded content).
    intake_summary_text is the full structured prompt section.
    proposed_action_json is a JSON-encoded EmailSignals dict (may be "{}").
    """
    from app.email.extractor import (
        EmailSignals,
        extract_signals,
        format_signals_for_summary,
        signals_to_json,
    )

    raw_text = (msg.text or "").strip()

    instruction, body = _split_instruction_and_body(raw_text)
    instruction = _clean(instruction)[:_MAX_INSTRUCTION_CHARS]
    body = _clean(body)[:_MAX_BODY_CHARS]

    # Extract structured signals from the full text before truncation
    signals: EmailSignals = extract_signals(raw_text)
    proposed_action_json = signals_to_json(signals)

    received = msg.timestamp.isoformat() if msg.timestamp else "unknown"

    lines: list[str] = [
        "## Email Intake",
        f"From: {msg.from_email}",
        f"Subject: {msg.subject}",
        f"Received: {received}",
    ]

    if instruction:
        lines += ["", "## User Instruction", instruction]

    signals_block = format_signals_for_summary(signals)
    if signals_block:
        lines += ["", signals_block]

    if body:
        lines += ["", "## Email Body", body]
    elif raw_text and not instruction:
        cleaned = _clean(raw_text)[:_MAX_BODY_CHARS]
        lines += ["", "## Email Body", cleaned]

    if msg.attachments:
        att_lines = []
        for a in msg.attachments[:5]:
            name = a.get("filename", "unnamed")
            ct = a.get("content_type", "")
            sz = a.get("size", 0)
            att_lines.append(f"  - {name} ({ct}, {sz} B) [not downloaded]")
        lines += ["", "## Attachments"] + att_lines

    summary = "\n".join(lines)

    # Final hard cap
    if len(summary) > max_chars:
        summary = summary[:max_chars] + "\n[truncated]"

    return instruction, summary, proposed_action_json


def build_telegram_prompt(msg: AgentMailMessage, instruction: str) -> str:
    """Build the short Telegram confirmation message shown to the user."""
    subject = msg.subject[:80] if msg.subject else "(no subject)"
    if instruction:
        body = f'"{instruction[:300]}"'
    else:
        preview = (msg.text or "")[:200].strip()
        body = f'"{preview}"' if preview else "(no body)"

    return (
        f"📧 Email from {msg.from_email}:\n"
        f"Subject: {subject}\n\n"
        f"{body}\n\n"
        f"Process this email?"
    )
