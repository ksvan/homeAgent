"""Tests for app/agent/prompts.py — static/dynamic prompt split.

Verifies persona.md + instructions.md form a stable, variable-free static
body (for Agent(instructions=...), see docs/prompt-caching-design.md), and
that identity.md carries the one legitimately per-call-varying piece.
"""
from __future__ import annotations

from app.agent.prompts import (
    load_static_prompt_body,
    render_identity_block,
)


def test_static_prompt_body_is_byte_identical_across_calls() -> None:
    first = load_static_prompt_body()
    second = load_static_prompt_body()
    assert first == second


def test_static_prompt_body_has_no_identity_placeholders() -> None:
    body = load_static_prompt_body()
    assert "{agent_name}" not in body
    assert "{household_name}" not in body
    assert "{user_name}" not in body


def test_static_prompt_body_is_nonempty_and_contains_persona_content() -> None:
    body = load_static_prompt_body()
    assert "Be brief" in body
    assert "household helper" in body.lower()


def test_static_prompt_body_contains_groceries_section() -> None:
    # See docs/oda-grocery-mcp-tool-design.md "Agent behaviour" — kept
    # deliberately brief, only the three explicitly-requested rules.
    body = load_static_prompt_body()
    assert "Groceries (Oda)" in body
    assert "No budget constraints" in body


def test_static_prompt_body_preserves_json_examples_from_instructions() -> None:
    # instructions.md uses {{...}} escaping for JSON examples that must survive
    # the format_map pass unescaped to single braces, not left doubled.
    body = load_static_prompt_body()
    assert '{"eq": true}' in body
    assert '{{"eq": true}}' not in body


def test_render_identity_block_contains_passed_names() -> None:
    block = render_identity_block("Visvas", "Svantorp", "Kristian")
    assert "Visvas" in block
    assert "Svantorp" in block
    assert "Kristian" in block


def test_render_identity_block_varies_by_input() -> None:
    a = render_identity_block("Visvas", "Household A", "Alice")
    b = render_identity_block("Visvas", "Household B", "Bob")
    assert a != b


def test_identity_and_static_body_together_cover_old_persona_intent() -> None:
    # The old persona.md rendered "You are {agent_name}, the AI assistant for
    # the {household_name} household. You are currently speaking with
    # {user_name}." — confirm that sentence structure survives, just split
    # across the two sources instead of one.
    identity = render_identity_block("Visvas", "Svantorp", "Kristian")
    assert "AI assistant" in identity
    assert "currently speaking with" in identity
