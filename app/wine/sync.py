from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from app.wine.models import WineSyncResult

logger = logging.getLogger(__name__)

_sync_lock = asyncio.Lock()


async def sync_wine_cellar(force: bool = False) -> WineSyncResult:
    """
    Single entry point for all wine sync triggers.

    - force=False: skip download if eTag matches and cache is within TTL.
    - force=True: always download (used by the refresh_wine_cellar tool).

    A module-level lock ensures that concurrent callers wait for the
    in-flight sync rather than triggering duplicate Graph downloads.
    """
    async with _sync_lock:
        return await _sync(force=force)


async def _sync(force: bool) -> WineSyncResult:
    from app.config import get_settings
    from app.control.events import emit
    from app.wine import graph_client, repository
    from app.wine.parser import parse_xlsx, rows_to_bottles

    settings = get_settings()

    # Config check — all required credentials must be set
    required = [
        settings.wine_graph_tenant_id,
        settings.wine_graph_client_id,
        settings.wine_graph_client_secret,
        settings.wine_graph_drive_id,
        settings.wine_graph_item_id,
    ]
    if not all(required):
        err = "Wine cellar Graph credentials not fully configured"
        logger.warning(err)
        return WineSyncResult(success=False, error=err)

    emit("wine.sync_started", {})

    t0 = time.monotonic()
    now = datetime.now(timezone.utc)

    # Check whether we can skip the download
    if not force:
        meta = repository.get_sync_meta()
        if meta is not None:
            # Within TTL?
            last_sync = meta.last_sync_at
            last_sync_aware = last_sync.replace(tzinfo=timezone.utc) if last_sync else None
            ttl = settings.wine_cache_ttl_seconds
            if last_sync_aware and (now - last_sync_aware).total_seconds() < ttl:
                # Quick eTag check to see if the file has changed
                try:
                    current_etag = await graph_client.get_item_etag(
                        settings.wine_graph_tenant_id,
                        settings.wine_graph_client_id,
                        settings.wine_graph_client_secret,
                        settings.wine_graph_drive_id,
                        settings.wine_graph_item_id,
                    )
                    if current_etag and current_etag == meta.etag:
                        bottles = repository.get_all_bottles()
                        import json
                        warnings = json.loads(meta.parse_warnings or "[]")
                        result = WineSyncResult(
                            success=True,
                            row_count=len(bottles),
                            etag=str(meta.etag),
                            synced_at=last_sync,
                            stale=False,
                            parse_warnings=warnings,
                        )
                        logger.debug("Wine cache hit (eTag unchanged)")
                        return result
                except Exception:
                    logger.debug("eTag check failed, proceeding with download", exc_info=True)

    # Download and parse
    try:
        content, etag = await graph_client.download_workbook(
            settings.wine_graph_tenant_id,
            settings.wine_graph_client_id,
            settings.wine_graph_client_secret,
            settings.wine_graph_drive_id,
            settings.wine_graph_item_id,
        )
    except Exception as exc:
        err = f"Graph download failed: {exc}"
        logger.error(err, exc_info=True)
        repository.record_sync_attempt(error=err)

        # Return stale cache if available
        bottles = repository.get_all_bottles()
        meta = repository.get_sync_meta()
        if bottles and meta is not None:
            import json
            warnings = json.loads(meta.parse_warnings or "[]")
            emit("wine.cache_used_stale", {"error": err})
            return WineSyncResult(
                success=True,
                row_count=len(bottles),
                etag=str(meta.etag),
                synced_at=meta.last_sync_at,
                stale=True,
                parse_warnings=warnings,
                error=err,
            )
        emit("wine.sync_failed", {"error": err})
        return WineSyncResult(success=False, error=err)

    try:
        rows, warnings = parse_xlsx(
            content,
            etag,
            worksheet_name=settings.wine_worksheet_name,
            table_name=settings.wine_excel_table_name,
        )
    except Exception as exc:
        err = f"Failed to parse wine workbook: {exc}"
        logger.error(err, exc_info=True)
        repository.record_sync_attempt(error=err)
        emit("wine.sync_failed", {"error": err})
        return WineSyncResult(success=False, error=err)

    bottles = rows_to_bottles(rows)
    repository.upsert_snapshot(bottles, etag, warnings)

    elapsed = time.monotonic() - t0
    logger.info(
        "Wine sync complete: %d bottles parsed in %.1fs (etag=%s warnings=%d)",
        len(bottles), elapsed, etag[:8] if etag else "?", len(warnings),
    )
    emit("wine.sync_complete", {"row_count": len(bottles), "elapsed_s": round(elapsed, 2)})

    return WineSyncResult(
        success=True,
        row_count=len(bottles),
        etag=etag,
        synced_at=now,
        stale=False,
        parse_warnings=warnings,
    )
