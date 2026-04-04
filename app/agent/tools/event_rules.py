from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from pydantic_ai import Agent, RunContext

from app.agent.agent import AgentDeps

logger = logging.getLogger(__name__)


def register_event_rule_tools(agent: Agent[AgentDeps, str]) -> None:
    """Attach event-rule management tools to the conversation agent."""

    @agent.tool
    async def create_event_rule(
        ctx: RunContext[AgentDeps],
        name: str,
        prompt_template: str,
        event_type: str = "*",
        entity_id: str = "*",
        capability: str | None = None,
        source: str = "homey",
        cooldown_minutes: int = 5,
        value_filter_json: str | None = None,
        condition_json: str | None = None,
        run_mode: str = "notify_only",
        task_kind_default: str | None = None,
        correlation_key_tpl: str | None = None,
    ) -> str:
        """Create a standing event rule that wakes the agent when a device event fires.

        Event rules are persistent reactive triggers — they stay active until
        explicitly deleted or disabled. Use this when the user wants ongoing
        autonomous monitoring or alerts, e.g.:
          - "Alert me whenever the front door opens after 22:00"
          - "Watch the living room motion sensor and adjust lights automatically"
          - "Notify me if any window is left open when temperature drops below 5°C"

        Confirm with the user before creating a rule. After creating, confirm the
        name, trigger criteria, and schedule back to the user.

        Args:
            name: Short descriptive label, e.g. "Front door alert after 22:00".
            prompt_template: Instruction sent to the agent when the rule fires.
                Supports {entity_id}, {entity_name}, {capability}, {value},
                {zone}, {time} interpolation.
                Example: "The front door ({entity_name}) opened at {time}.
                          The household is likely asleep. Notify the user."
            event_type: Homey event type to match. Usually "device_state_change".
                Use "*" to match any event type. Default "*".
            entity_id: Device UUID to watch, or "*" for any device. Get the UUID
                from homey_get_home_structure or homey_get_states. Default "*".
            capability: Capability name to filter on, e.g. "onoff", "alarm_motion",
                "measure_temperature". Leave empty to match any capability.
            source: Event source. Currently always "homey". Default "homey".
            cooldown_minutes: Minimum minutes between firings for this rule.
                Prevents flooding. Default 5. Set higher for noisy sensors.
            value_filter_json: Optional JSON filter on the capability value.
                Examples: '{"eq": true}' (only when value is true/on),
                '{"gt": 22.5}' (only when value exceeds 22.5),
                '{"ne": null}' (any non-null value). Leave empty for no filter.
            condition_json: Optional JSON delivery conditions.
                Example: '{"quiet_hours_start": "22:00", "quiet_hours_end": "07:00"}'
                to only fire outside quiet hours.
            run_mode: "notify_only" (default) — wake agent once per event.
                "task_loop" — create/reuse a durable control Task; keeps events
                correlated across multiple firings for ongoing autonomous work.
            task_kind_default: Task kind when run_mode is "task_loop".
                One of: track, plan, prepare, handoff. Default "track".
            correlation_key_tpl: Custom correlation key template for task_loop mode.
                Default: "rule:{rule_id}:entity:{entity_id}".
        """
        from sqlmodel import select

        from app.db import users_session
        from app.models.events import EventRule
        from app.models.users import User

        household_id = ctx.deps.household_id
        user_id = ctx.deps.user_id
        channel_user_id = ctx.deps.channel_user_id

        if not household_id or not user_id:
            return "Cannot create event rule — session context is missing household or user."

        # Validate JSON fields
        for field_val, field_name in [
            (value_filter_json, "value_filter_json"),
            (condition_json, "condition_json"),
        ]:
            if field_val:
                try:
                    json.loads(field_val)
                except json.JSONDecodeError as exc:
                    return f"Invalid JSON in {field_name}: {exc}"

        # Resolve channel_user_id from user record if not available in deps
        if not channel_user_id:
            with users_session() as session:
                user = session.exec(select(User).where(User.id == user_id)).first()
            if not user:
                return f"User {user_id!r} not found — cannot create event rule."
            channel_user_id = str(user.telegram_id)

        now = datetime.now(timezone.utc)
        rule = EventRule(
            household_id=household_id,
            user_id=user_id,
            channel_user_id=channel_user_id,
            name=name,
            source=source,
            event_type=event_type,
            entity_id=entity_id,
            capability=capability or None,
            value_filter_json=value_filter_json or None,
            condition_json=condition_json or None,
            cooldown_minutes=cooldown_minutes,
            prompt_template=prompt_template,
            run_mode=run_mode,
            task_kind_default=task_kind_default or None,
            correlation_key_tpl=correlation_key_tpl or None,
            enabled=True,
            created_at=now,
            updated_at=now,
        )
        with users_session() as session:
            session.add(rule)
            session.commit()
            session.refresh(rule)

        logger.info(
            "EventRule created by agent: id=%s name=%r user=%s", rule.id, rule.name, user_id
        )

        match_desc = (
            f"source={rule.source}, event_type={rule.event_type}, entity_id={rule.entity_id}"
        )
        if rule.capability:
            match_desc += f", capability={rule.capability}"
        if rule.value_filter_json:
            match_desc += f", value_filter={rule.value_filter_json}"
        if rule.condition_json:
            match_desc += f", condition={rule.condition_json}"

        return (
            f"Event rule created.\n"
            f"ID: {rule.id}\n"
            f"Name: {rule.name}\n"
            f"Trigger: {match_desc}\n"
            f"Cooldown: {rule.cooldown_minutes} minutes\n"
            f"Mode: {rule.run_mode}\n"
            f"Status: enabled"
        )

    @agent.tool
    async def list_event_rules(ctx: RunContext[AgentDeps]) -> str:
        """List all active event rules for this household.

        Use this before creating a new rule (to avoid duplicates) or before
        deleting one (to confirm the correct ID and name).
        """
        from sqlmodel import select

        from app.db import users_session
        from app.models.events import EventRule

        household_id = ctx.deps.household_id
        if not household_id:
            return "Cannot list event rules — household context is missing."

        with users_session() as session:
            rules = session.exec(
                select(EventRule)
                .where(EventRule.household_id == household_id)
                .order_by(EventRule.created_at)
            ).all()

        if not rules:
            return "No event rules defined for this household."

        lines = []
        for r in rules:
            status = "enabled" if r.enabled else "disabled"
            match = f"source={r.source}, event_type={r.event_type}, entity_id={r.entity_id}"
            if r.capability:
                match += f", cap={r.capability}"
            last = (
                r.last_triggered_at.strftime("%Y-%m-%d %H:%M")
                if r.last_triggered_at else "never"
            )
            lines.append(
                f"- [{status}] {r.name} (id={r.id})\n"
                f"  Match: {match}\n"
                f"  Cooldown: {r.cooldown_minutes}m | Mode: {r.run_mode} | Last fired: {last}"
            )

        return f"{len(rules)} event rule(s):\n\n" + "\n\n".join(lines)

    @agent.tool
    async def delete_event_rule(
        ctx: RunContext[AgentDeps],
        rule_id: str,
    ) -> str:
        """Delete an event rule permanently by its ID.

        Use list_event_rules first to confirm the correct ID and name.
        Prefer disabling (disable_event_rule) over deleting if the user may
        want to re-enable later.
        """
        from app.db import users_session
        from app.models.events import EventRule

        household_id = ctx.deps.household_id

        with users_session() as session:
            rule = session.get(EventRule, rule_id)
            if not rule:
                return f"No event rule found with id={rule_id!r}."
            if rule.household_id != household_id:
                return "Rule not found in this household."
            name = rule.name
            session.delete(rule)
            session.commit()

        logger.info("EventRule deleted by agent: id=%s name=%r", rule_id, name)
        return f"Event rule '{name}' (id={rule_id}) deleted."

    @agent.tool
    async def disable_event_rule(
        ctx: RunContext[AgentDeps],
        rule_id: str,
    ) -> str:
        """Disable an event rule without deleting it.

        The rule stays in the database and can be re-enabled later via
        enable_event_rule. Prefer this over deletion when the user might want
        to reactivate the rule in the future.
        """
        from datetime import datetime, timezone

        from app.db import users_session
        from app.models.events import EventRule

        household_id = ctx.deps.household_id

        with users_session() as session:
            rule = session.get(EventRule, rule_id)
            if not rule:
                return f"No event rule found with id={rule_id!r}."
            if rule.household_id != household_id:
                return "Rule not found in this household."
            if not rule.enabled:
                return f"Rule '{rule.name}' is already disabled."
            rule.enabled = False
            rule.updated_at = datetime.now(timezone.utc)
            session.add(rule)
            session.commit()
            name = rule.name

        logger.info("EventRule disabled by agent: id=%s name=%r", rule_id, name)
        return f"Event rule '{name}' (id={rule_id}) disabled."

    @agent.tool
    async def enable_event_rule(
        ctx: RunContext[AgentDeps],
        rule_id: str,
    ) -> str:
        """Re-enable a previously disabled event rule."""
        from datetime import datetime, timezone

        from app.db import users_session
        from app.models.events import EventRule

        household_id = ctx.deps.household_id

        with users_session() as session:
            rule = session.get(EventRule, rule_id)
            if not rule:
                return f"No event rule found with id={rule_id!r}."
            if rule.household_id != household_id:
                return "Rule not found in this household."
            if rule.enabled:
                return f"Rule '{rule.name}' is already enabled."
            rule.enabled = True
            rule.updated_at = datetime.now(timezone.utc)
            session.add(rule)
            session.commit()
            name = rule.name

        logger.info("EventRule enabled by agent: id=%s name=%r", rule_id, name)
        return f"Event rule '{rule.name}' (id={rule_id}) enabled."
