from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Valid day-of-week abbreviations accepted by APScheduler CronTrigger
_VALID_DAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}


def _build_trigger(recurrence: str, time_of_day: str, run_at: object = None) -> object:
    """Return an APScheduler trigger for the given recurrence + time."""
    if recurrence == "once":
        from apscheduler.triggers.date import DateTrigger

        if run_at is None:
            raise ValueError("run_at is required when recurrence='once'")
        return DateTrigger(run_time=run_at)

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
        "Use 'daily', 'weekly:mon', 'weekly:sun', 'monthly:15', or 'once'."
    )


def recurrence_label(recurrence: str, time_of_day: str, run_at: object = None) -> str:
    """Return a human-readable label, e.g. 'Every Sunday at 20:00'."""
    if recurrence == "once":
        if run_at is not None:
            from datetime import datetime
            dt = run_at if isinstance(run_at, datetime) else run_at
            return f"Once on {dt.strftime('%d %b %Y at %H:%M')}"
        return "Once (time unknown)"
    if recurrence == "daily":
        return f"Every day at {time_of_day}"
    if recurrence.startswith("weekly:"):
        day = recurrence[len("weekly:"):]
        return f"Every {day.capitalize()} at {time_of_day}"
    if recurrence.startswith("monthly:"):
        day_num = recurrence[len("monthly:"):]
        return f"Monthly on the {day_num} at {time_of_day}"
    return f"{recurrence} at {time_of_day}"


async def create_scheduled_prompt(
    user_id: str,
    household_id: str,
    channel_user_id: str,
    name: str,
    prompt: str,
    recurrence: str,
    time_of_day: str,
    run_at: object = None,
) -> str:
    """
    Persist a ScheduledPrompt record and register the APScheduler job.

    For recurring prompts (daily/weekly/monthly), uses CronTrigger.
    For one-shot prompts (recurrence="once"), uses DateTrigger with run_at.

    Returns the prompt_id which can be used to cancel the prompt.
    Raises ValueError for invalid recurrence / time_of_day / run_at.
    Raises RuntimeError if scheduler registration fails (DB record disabled).
    """
    from apscheduler import ConflictPolicy

    from app.db import users_session
    from app.models.scheduled_prompts import ScheduledPrompt
    from app.scheduler.engine import get_scheduler
    from app.scheduler.jobs import fire_scheduled_prompt

    trigger = _build_trigger(recurrence, time_of_day, run_at)  # validates early
    is_one_shot = recurrence == "once"

    with users_session() as session:
        sp = ScheduledPrompt(
            household_id=household_id,
            user_id=user_id,
            channel_user_id=channel_user_id,
            name=name,
            prompt=prompt,
            recurrence=recurrence,
            time_of_day=time_of_day,
            run_at=run_at,
        )
        session.add(sp)
        session.commit()
        session.refresh(sp)
        prompt_id = sp.id

    scheduler = get_scheduler()
    if scheduler is not None:
        try:
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
                    "is_one_shot": is_one_shot,
                },
            )
        except Exception as exc:
            with users_session() as s:
                sp_rec = s.get(ScheduledPrompt, prompt_id)
                if sp_rec:
                    sp_rec.enabled = False
                    s.add(sp_rec)
                    s.commit()
            raise RuntimeError(f"Scheduler registration failed: {exc}") from exc
    else:
        logger.warning("Scheduler not running — prompt_id=%s will not fire", prompt_id)

    logger.info(
        "Scheduled prompt created: prompt_id=%s recurrence=%s time=%s run_at=%s name=%r",
        prompt_id,
        recurrence,
        time_of_day,
        run_at,
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
        # One-shot prompts whose fire time has passed are stale — delete them.
        if sp.recurrence == "once":
            from datetime import datetime, timezone as _tz
            if sp.run_at is None or sp.run_at <= datetime.now(_tz.utc):
                with users_session() as s:
                    stale = s.get(ScheduledPrompt, sp.id)
                    if stale:
                        s.delete(stale)
                        s.commit()
                logger.info(
                    "Deleted stale one-shot prompt_id=%s (run_at=%s)", sp.id, sp.run_at
                )
                continue

        is_one_shot = sp.recurrence == "once"
        try:
            trigger = _build_trigger(sp.recurrence, sp.time_of_day, sp.run_at)
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
                    "is_one_shot": is_one_shot,
                },
            )
            restored += 1
        except Exception as exc:
            logger.warning(
                "Skipping scheduled prompt_id=%s during restore: %s", sp.id, exc, exc_info=True
            )

    if restored:
        logger.info("Restored %d scheduled prompt(s) from DB", restored)
