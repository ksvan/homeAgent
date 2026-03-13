from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Valid day-of-week abbreviations accepted by APScheduler CronTrigger
_VALID_DAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}


def _build_trigger(recurrence: str, time_of_day: str) -> object:
    """Return an APScheduler CronTrigger for the given recurrence + time."""
    from apscheduler.triggers.cron import CronTrigger

    try:
        hour_str, minute_str = time_of_day.split(":")
        hour = int(hour_str)
        minute = int(minute_str)
    except ValueError:
        raise ValueError(f"Invalid time_of_day {time_of_day!r}; expected 'HH:MM'")

    if recurrence == "daily":
        return CronTrigger(hour=hour, minute=minute)

    if recurrence.startswith("weekly:"):
        day = recurrence[len("weekly:"):]
        if day not in _VALID_DAYS:
            raise ValueError(
                f"Unknown day {day!r}. Use one of: {', '.join(sorted(_VALID_DAYS))}"
            )
        return CronTrigger(day_of_week=day, hour=hour, minute=minute)

    if recurrence.startswith("monthly:"):
        try:
            day_num = int(recurrence[len("monthly:"):])
        except ValueError:
            raise ValueError(f"Invalid monthly recurrence {recurrence!r}; expected 'monthly:N'")
        if not 1 <= day_num <= 28:
            raise ValueError("Monthly day must be 1–28 to work across all months")
        return CronTrigger(day=day_num, hour=hour, minute=minute)

    raise ValueError(
        f"Unknown recurrence {recurrence!r}. "
        "Use 'daily', 'weekly:mon', 'weekly:sun', 'monthly:15', etc."
    )


def recurrence_label(recurrence: str, time_of_day: str) -> str:
    """Return a human-readable label, e.g. 'Every Sunday at 20:00'."""
    if recurrence == "daily":
        return f"Every day at {time_of_day}"
    if recurrence.startswith("weekly:"):
        day = recurrence[len("weekly:"):]
        return f"Every {day.capitalize()} at {time_of_day}"
    if recurrence.startswith("monthly:"):
        day_num = recurrence[len("monthly:"):]
        return f"Monthly on the {day_num} at {time_of_day}"
    return f"{recurrence} at {time_of_day}"


def create_scheduled_prompt(
    user_id: str,
    household_id: str,
    channel_user_id: str,
    name: str,
    prompt: str,
    recurrence: str,
    time_of_day: str,
) -> str:
    """
    Persist a ScheduledPrompt record and register the CronTrigger APScheduler job.

    Returns the prompt_id which can be used to cancel the prompt.
    Raises ValueError for invalid recurrence / time_of_day.
    """
    from app.db import users_session
    from app.models.scheduled_prompts import ScheduledPrompt
    from app.scheduler.engine import get_scheduler
    from app.scheduler.jobs import fire_scheduled_prompt

    trigger = _build_trigger(recurrence, time_of_day)  # validates early

    with users_session() as session:
        sp = ScheduledPrompt(
            household_id=household_id,
            user_id=user_id,
            channel_user_id=channel_user_id,
            name=name,
            prompt=prompt,
            recurrence=recurrence,
            time_of_day=time_of_day,
        )
        session.add(sp)
        session.commit()
        session.refresh(sp)
        prompt_id = sp.id

    scheduler = get_scheduler()
    if scheduler is not None:
        import asyncio
        from apscheduler import ConflictPolicy

        async def _add() -> None:
            await scheduler.add_schedule(
                fire_scheduled_prompt,
                trigger,
                id=prompt_id,
                conflict_policy=ConflictPolicy.replace,
                kwargs={
                    "prompt_id": prompt_id,
                    "user_id": user_id,
                    "household_id": household_id,
                    "channel_user_id": channel_user_id,
                    "prompt_text": prompt,
                    "name": name,
                },
            )

        asyncio.ensure_future(_add())
    else:
        logger.warning("Scheduler not running — prompt_id=%s will not fire", prompt_id)

    logger.info(
        "Scheduled prompt created: prompt_id=%s recurrence=%s time=%s name=%r",
        prompt_id,
        recurrence,
        time_of_day,
        name,
    )
    return prompt_id


def remove_scheduled_prompt(prompt_id: str) -> None:
    """Disable a ScheduledPrompt in DB and remove the APScheduler job."""
    from app.db import users_session
    from app.models.scheduled_prompts import ScheduledPrompt
    from app.scheduler.engine import get_scheduler

    with users_session() as session:
        sp = session.get(ScheduledPrompt, prompt_id)
        if sp is not None:
            sp.enabled = False
            session.add(sp)
            session.commit()

    scheduler = get_scheduler()
    if scheduler is not None:
        import asyncio

        async def _remove() -> None:
            try:
                await scheduler.remove_schedule(prompt_id)
            except Exception:
                pass

        asyncio.ensure_future(_remove())


async def restore_scheduled_prompts() -> None:
    """
    Called on startup — re-register CronTrigger jobs for all enabled ScheduledPrompts.

    Uses ConflictPolicy.replace so restarts are idempotent.
    """
    from apscheduler import ConflictPolicy
    from sqlmodel import select

    from app.db import users_session
    from app.models.scheduled_prompts import ScheduledPrompt
    from app.scheduler.engine import get_scheduler
    from app.scheduler.jobs import fire_scheduled_prompt

    scheduler = get_scheduler()
    if scheduler is None:
        return

    with users_session() as session:
        prompts = session.exec(
            select(ScheduledPrompt).where(ScheduledPrompt.enabled == True)  # noqa: E712
        ).all()

    restored = 0
    for sp in prompts:
        try:
            trigger = _build_trigger(sp.recurrence, sp.time_of_day)
        except ValueError:
            logger.warning(
                "Skipping scheduled prompt with invalid recurrence: prompt_id=%s recurrence=%r",
                sp.id,
                sp.recurrence,
            )
            continue

        await scheduler.add_schedule(
            fire_scheduled_prompt,
            trigger,
            id=sp.id,
            conflict_policy=ConflictPolicy.replace,
            kwargs={
                "prompt_id": sp.id,
                "user_id": sp.user_id,
                "household_id": sp.household_id,
                "channel_user_id": sp.channel_user_id,
                "prompt_text": sp.prompt,
                "name": sp.name,
            },
        )
        restored += 1

    if restored:
        logger.info("Restored %d scheduled prompt(s) from DB", restored)
