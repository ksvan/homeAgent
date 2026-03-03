from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from pydantic_ai import Agent, RunContext

from app.agent.agent import AgentDeps

logger = logging.getLogger(__name__)


def register_scrape_tools(agent: Agent[AgentDeps, str]) -> None:
    """Attach the web scraping tool to the conversation agent."""

    @agent.tool
    async def scrape_web_page(
        ctx: RunContext[AgentDeps],
        url: str,
        timeout_s: int = 20,
    ) -> str:
        """Fetch a web page and return its readable text content.

        Use this when the user asks to look something up online, check a website,
        read an article, or get information from a specific URL. Only use this
        when the user explicitly asks for a web fetch — do not browse speculatively.

        Returns the page title and main text content with boilerplate (nav, ads,
        scripts) removed. Links and images are not included.

        Args:
            url:       The full URL to fetch (http or https).
            timeout_s: Request timeout in seconds (default 20, max 60).
        """
        import httpx
        from bs4 import BeautifulSoup

        from app.config import get_settings

        settings = get_settings()
        timeout_s = min(timeout_s, settings.scrape_timeout_seconds)

        # Validate URL
        try:
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                return f"Invalid URL: {url!r}. Only http/https URLs are supported."
        except Exception:
            return f"Could not parse URL: {url!r}"

        logger.info("Scraping URL: %s", url)

        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=timeout_s,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; HomeAgent/1.0)",
                    "Accept-Language": "en,*;q=0.5",
                },
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return f"HTTP {exc.response.status_code} from {url}"
        except httpx.TimeoutException:
            return f"Request timed out after {timeout_s}s: {url}"
        except Exception as exc:
            return f"Failed to fetch {url}: {exc}"

        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type and "application/xhtml" not in content_type:
            # Non-HTML response — return raw text up to limit
            text = response.text[: settings.scrape_max_content_bytes]
            return f"[{content_type}]\n{text}"

        soup = BeautifulSoup(response.text, "html.parser")

        # Extract title
        title = ""
        if soup.title and soup.title.string:
            title = soup.title.string.strip()

        # Remove boilerplate tags
        for tag in soup(
            ["script", "style", "head", "nav", "footer", "header", "aside",
             "form", "button", "iframe", "noscript"]
        ):
            tag.decompose()

        # Extract text
        text = soup.get_text(separator="\n", strip=True)

        # Collapse excessive whitespace
        text = re.sub(r" {2,}", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = text.strip()

        # Truncate
        max_bytes = settings.scrape_max_content_bytes
        if len(text.encode("utf-8")) > max_bytes:
            text = text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
            text += "\n\n[Content truncated]"

        result = f"# {title}\n\n{text}" if title else text
        logger.info("Scraped %s — %d chars returned", url, len(result))
        return result
