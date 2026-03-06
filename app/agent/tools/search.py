from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from pydantic_ai import Agent, RunContext

from app.agent.agent import AgentDeps

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data contract — shared across all providers
# ---------------------------------------------------------------------------


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str  # short text excerpt from the page


# ---------------------------------------------------------------------------
# Provider interface — implement this Protocol to add a new search backend
# ---------------------------------------------------------------------------


class SearchProvider(Protocol):
    async def search(self, query: str, max_results: int) -> list[SearchResult]: ...


# ---------------------------------------------------------------------------
# Concrete providers
# ---------------------------------------------------------------------------


class TavilyProvider:
    """Tavily Search API — https://tavily.com

    To swap this out, create a class that satisfies SearchProvider and add a
    branch in _get_provider() keyed on the SEARCH_PROVIDER env var.
    """

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        from tavily import AsyncTavilyClient  # type: ignore[import-untyped]

        client = AsyncTavilyClient(api_key=self._api_key)
        response = await client.search(query, max_results=max_results)
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("content", ""),
            )
            for r in response.get("results", [])
        ]


# ---------------------------------------------------------------------------
# Factory — reads SEARCH_PROVIDER from config, returns the right provider
# ---------------------------------------------------------------------------


def _get_provider() -> SearchProvider:
    from app.config import get_settings

    settings = get_settings()
    name = settings.search_provider.lower()

    if name == "tavily":
        if not settings.tavily_api_key:
            raise RuntimeError(
                "TAVILY_API_KEY is required when SEARCH_PROVIDER=tavily. "
                "Sign up at https://tavily.com and add the key to .env."
            )
        return TavilyProvider(api_key=settings.tavily_api_key)

    raise ValueError(
        f"Unknown search provider: {name!r}. "
        "Set SEARCH_PROVIDER to a supported value (currently: 'tavily')."
    )


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register_search_tools(agent: Agent[AgentDeps, str]) -> None:
    @agent.tool
    async def search_web(
        ctx: RunContext[AgentDeps],
        query: str,
        max_results: int = 5,
    ) -> str:
        """Search the web and return a list of results with title, URL, and excerpt.

        Use this when the user asks for current information, news, prices, events,
        how-to guides, or anything that might have changed since your training data.

        Do NOT use this for a specific URL the user provides — use scrape_web_page
        instead. Call scrape_web_page on a result URL if you need the full content.

        Args:
            query:       The search query. Be specific; include year or context if relevant.
            max_results: Number of results to return (1–10, default 5).
        """
        from app.config import get_settings

        settings = get_settings()
        max_results = min(max(1, max_results), settings.search_max_results)

        logger.info("Web search: %r (max_results=%d)", query, max_results)

        try:
            provider = _get_provider()
            results = await provider.search(query, max_results)
        except Exception as exc:
            logger.exception("Search failed")
            return f"Search failed: {exc}"

        if not results:
            return "No results found."

        lines: list[str] = []
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r.title}")
            lines.append(f"   {r.url}")
            if r.snippet:
                snippet = r.snippet[:400].rstrip()
                if len(r.snippet) > 400:
                    snippet += "…"
                lines.append(f"   {snippet}")
            lines.append("")

        return "\n".join(lines).rstrip()
