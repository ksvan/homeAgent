from __future__ import annotations

import logging
from typing import Optional

from pydantic_ai import Agent, RunContext

from app.agent.agent import AgentDeps

logger = logging.getLogger(__name__)

# Food → category/keyword hints for ranking
_FOOD_HINTS: dict[str, list[str]] = {
    "fish": ["hvitvin", "white", "sparkling", "champagne", "chablis", "riesling", "sauvignon"],
    "shellfish": ["hvitvin", "white", "sparkling", "champagne", "chablis", "riesling", "sauvignon"],
    "seafood": ["hvitvin", "white", "sparkling", "champagne", "riesling", "sauvignon"],
    "lamb": ["rødvin", "red", "bordeaux", "rioja", "barolo", "barbaresco", "rhône", "syrah",
             "cabernet", "nebbiolo", "merlot"],
    "beef": ["rødvin", "red", "bordeaux", "rioja", "barolo", "cabernet", "syrah", "malbec"],
    "game": ["rødvin", "red", "barolo", "barbaresco", "rhône", "syrah", "cabernet", "nebbiolo"],
    "pork": ["chardonnay", "pinot", "hvitvin", "white", "rødvin", "red"],
    "chicken": ["chardonnay", "pinot", "hvitvin", "white", "rødvin", "red"],
    "spicy": ["riesling", "gewürztraminer", "hvitvin", "white", "sparkling"],
    "asian": ["riesling", "gewürztraminer", "hvitvin", "white", "sparkling"],
    "dessert": ["søt", "sweet", "sauternes", "port", "dessert"],
}


def _food_score(bottle: object, food: str) -> int:
    """Return a food-match score (higher = better match)."""
    from app.wine.models import WineBottle

    assert isinstance(bottle, WineBottle)
    keywords = _FOOD_HINTS.get(food.lower().strip(), [])
    if not keywords:
        return 0

    haystack = " ".join(
        (v or "").lower()
        for v in [bottle.category, bottle.name, bottle.producer, bottle.region, bottle.note]
    )
    return sum(1 for kw in keywords if kw in haystack)


def _drink_urgency(bottle: object) -> int:
    """0 = unknown/hold, higher = more urgent."""
    from app.wine.models import WineBottle

    assert isinstance(bottle, WineBottle)
    status = bottle.drink_status
    return {"drink_now": 2, "past_window": 1, "hold": 0, "unknown": 0}[status]


def _bottle_to_dict(bottle: object) -> dict[str, object]:
    from app.wine.models import WineBottle

    assert isinstance(bottle, WineBottle)
    return {
        "id": bottle.id,
        "display_name": bottle.display_name,
        "category": bottle.category,
        "country": bottle.country,
        "producer": bottle.producer,
        "name": bottle.name,
        "vintage": bottle.vintage,
        "region": bottle.region,
        "score": bottle.score,
        "purchase_price_nok": bottle.purchase_price_nok,
        "drink_status": bottle.drink_status,
        "drink_window_end": str(bottle.drink_window_end) if bottle.drink_window_end else None,
        "note": bottle.note,
        "shelf": bottle.shelf,
    }


def register_wine_tools(agent: Agent[AgentDeps, str]) -> None:
    """Attach wine cellar tools to the conversation agent."""

    @agent.tool
    async def search_wine_cellar(
        ctx: RunContext[AgentDeps],
        name_search: Optional[str] = None,
        food: Optional[str] = None,
        occasion: Optional[str] = None,
        max_price_nok: Optional[float] = None,
        category: Optional[str] = None,
        country: Optional[str] = None,
        region: Optional[str] = None,
        include_consumed: bool = False,
        limit: Optional[int] = None,
    ) -> str:
        """Search the household wine cellar for available bottles.

        Use this tool when the user asks about wine pairing, wine availability,
        cellar contents, drink-window timing, or bottle recommendations.

        This tool is designed for iterative use: call it with broad filters first,
        then narrow by country, region, or category if needed.

        Args:
            name_search: Substring match on wine name and producer only.
            food: Food being served (e.g. "lamb", "fish", "beef"). Influences ranking.
            occasion: Occasion or mood (e.g. "celebration", "casual dinner").
            max_price_nok: Upper price limit in NOK.
            category: Filter by wine category (e.g. "Rødvin", "Hvitvin", "Champagne").
            country: Filter by country of origin.
            region: Substring match on wine district/region.
            include_consumed: If True, include already consumed bottles.
            limit: Max results to return. Defaults to configured WINE_SEARCH_DEFAULT_LIMIT.
        """
        import json

        from app.config import get_settings
        from app.wine.models import WineBottle
        from app.wine.repository import get_all_bottles
        from app.wine.sync import sync_wine_cellar

        settings = get_settings()
        eff_limit = limit if limit is not None else settings.wine_search_default_limit

        result = await sync_wine_cellar(force=False)
        if not result.success and not result.stale:
            return f"Wine cellar unavailable: {result.error}"

        bottles: list[WineBottle] = get_all_bottles()  # type: ignore[assignment]

        # --- Filter ---
        candidates = []
        for b in bottles:
            if not include_consumed and b.consumed:
                continue
            if category and (b.category or "").lower() != category.lower():
                # also try substring for flexibility
                if category.lower() not in (b.category or "").lower():
                    continue
            if country and country.lower() not in (b.country or "").lower():
                continue
            if region and region.lower() not in (b.region or "").lower():
                continue
            if max_price_nok is not None and b.purchase_price_nok is not None:
                if b.purchase_price_nok > max_price_nok:
                    continue
            if name_search:
                ns = name_search.lower()
                if ns not in (b.name or "").lower() and ns not in (b.producer or "").lower():
                    continue
            candidates.append(b)

        if not candidates:
            filters_used = [
                k for k, v in [
                    ("category", category), ("country", country), ("region", region),
                    ("name_search", name_search),
                ] if v
            ]
            hint = f" (filters: {', '.join(filters_used)})" if filters_used else ""
            return (
                f"No matching available bottles found{hint}. "
                "Try broadening the search by removing some filters."
            )

        # --- Rank ---
        food_key = (food or occasion or "").lower()

        if food or occasion:
            # Food/occasion query: food match first, then score, then urgency
            candidates.sort(
                key=lambda b: (
                    _food_score(b, food_key),
                    b.score or 0,
                    _drink_urgency(b),
                ),
                reverse=True,
            )
        elif not any([name_search, category, country, region, max_price_nok]):
            # Pure availability query: urgency first, then score
            candidates.sort(
                key=lambda b: (_drink_urgency(b), b.score or 0),
                reverse=True,
            )
        else:
            # Attribute/filter query: score first, then urgency
            candidates.sort(
                key=lambda b: (b.score or 0, _drink_urgency(b)),
                reverse=True,
            )

        top = candidates[:eff_limit]

        output: dict[str, object] = {
            "bottles": [_bottle_to_dict(b) for b in top],
            "total_matching": len(candidates),
            "showing": len(top),
        }
        if result.stale:
            output["warning"] = f"Showing cached inventory (last synced: {result.synced_at})"
        if result.parse_warnings:
            output["parse_warnings"] = result.parse_warnings

        return json.dumps(output, ensure_ascii=False, default=str)

    @agent.tool
    async def get_wine_cellar_summary(ctx: RunContext[AgentDeps]) -> str:
        """Get a summary of the household wine cellar: counts by category and country,
        drink-window warnings, and last sync time.

        Use for broad questions like "What wines do we have?" or "What should we drink soon?"
        """
        import json
        from collections import Counter

        from app.wine.models import WineBottle
        from app.wine.repository import get_all_bottles, get_sync_meta
        from app.wine.sync import sync_wine_cellar

        result = await sync_wine_cellar(force=False)
        if not result.success and not result.stale:
            return f"Wine cellar unavailable: {result.error}"

        bottles: list[WineBottle] = get_all_bottles()  # type: ignore[assignment]
        available = [b for b in bottles if b.available]

        cats: Counter[str] = Counter()
        countries: Counter[str] = Counter()
        drink_now = []
        past_window = []

        for b in available:
            if b.category:
                cats[b.category] += 1
            if b.country:
                countries[b.country] += 1
            if b.drink_status == "drink_now":
                drink_now.append(b.display_name)
            elif b.drink_status == "past_window":
                past_window.append(b.display_name)

        meta = get_sync_meta()
        last_sync = (
            meta.last_sync_at.strftime("%Y-%m-%d %H:%M UTC")  # type: ignore[union-attr]
            if meta and meta.last_sync_at  # type: ignore[union-attr]
            else "unknown"
        )

        summary: dict[str, object] = {
            "total_available": len(available),
            "total_in_cellar": len(bottles),
            "by_category": dict(cats.most_common()),
            "by_country": dict(countries.most_common()),
            "drink_now_count": len(drink_now),
            "past_window_count": len(past_window),
            "last_sync": last_sync,
        }
        if drink_now:
            summary["drink_now_examples"] = drink_now[:5]
        if past_window:
            summary["past_window_examples"] = past_window[:5]
        if result.stale:
            summary["warning"] = "Showing cached inventory"

        return json.dumps(summary, ensure_ascii=False, default=str)

    @agent.tool
    async def get_wine_bottle_detail(
        ctx: RunContext[AgentDeps],
        bottle_id: str,
    ) -> str:
        """Get the full details of a specific wine bottle by its ID.

        Args:
            bottle_id: The bottle ID from a previous search_wine_cellar result.
        """
        import json

        from app.wine.repository import get_bottle_by_id

        bottle = get_bottle_by_id(bottle_id)
        if bottle is None:
            return f"Bottle {bottle_id!r} not found. It may have been removed in the last sync."

        from app.wine.models import WineBottle

        assert isinstance(bottle, WineBottle)
        detail = _bottle_to_dict(bottle)
        detail["consumed"] = bottle.consumed
        detail["available"] = bottle.available
        detail["source_row"] = bottle.source_row
        return json.dumps(detail, ensure_ascii=False, default=str)

    @agent.tool
    async def refresh_wine_cellar(ctx: RunContext[AgentDeps]) -> str:
        """Force an immediate sync of the wine cellar from the Excel workbook.

        Use when the user explicitly asks to refresh or sync the wine inventory.
        """
        from app.wine.sync import sync_wine_cellar

        result = await sync_wine_cellar(force=True)
        if not result.success:
            return f"Refresh failed: {result.error}"
        return result.to_summary()
