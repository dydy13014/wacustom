import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from wastream.config.settings import settings
from wastream.utils.database import (
    SearchLock,
    database,
    run_database_transaction,
)
from wastream.utils.logger import database_logger


# ===========================
# History Configuration
# ===========================
_ALLOWED_CATEGORIES = frozenset({"source", "hoster"})
_ALLOWED_STATUSES = frozenset({
    "online",
    "offline",
    "unconfigured",
    "unknown",
    "checking",
})
HEALTH_HISTORY_PAGE_SIZE = 20
HEALTH_HISTORY_MAX_PAGE_SIZE = 100
HEALTH_HISTORY_CURSOR_MAX_LENGTH = 200
_MAX_HISTORY_TIMESTAMP = (2 ** 63) - 1


# ===========================
# Snapshot Normalization
# ===========================
def _normalize_snapshots(
    snapshots: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, str]]:
    normalized = {}
    for category, items in snapshots.items():
        if category not in _ALLOWED_CATEGORIES or not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            status = str(item.get("status") or "").strip().lower()
            if not name or status not in _ALLOWED_STATUSES:
                continue
            normalized[(category, name)] = {
                "category": category,
                "name": name,
                "status": status,
            }
    return list(normalized.values())


# ===========================
# Current Snapshot Lookup
# ===========================
async def _get_current_statuses() -> Dict[tuple, Any]:
    rows = await database.fetch_all(
        "SELECT event_id, category, name, status "
        "FROM health_status_history WHERE is_current = 1"
    )
    return {
        (str(row["category"]), str(row["name"])): row
        for row in rows
    }


# ===========================
# Status History Recording
# ===========================
def _new_event_id() -> str:
    return f"{time.time_ns():020d}-{uuid.uuid4()}"


async def record_health_status_snapshot(
    snapshots: Dict[str, List[Dict[str, Any]]],
) -> None:
    items = _normalize_snapshots(snapshots)
    if not items:
        return

    try:
        current_statuses = await _get_current_statuses()
        if all(
            (current := current_statuses.get(
                (item["category"], item["name"])
            )) is not None
            and str(current["status"]) == item["status"]
            for item in items
        ):
            return

        async with SearchLock(
            "health_status_history",
            "snapshot",
            wait=False,
        ) as history_lock:
            if not history_lock.acquired:
                return

            checked_at = int(time.time())

            async def write_snapshot() -> None:
                current_statuses = await _get_current_statuses()
                for item in items:
                    current = current_statuses.get(
                        (item["category"], item["name"])
                    )
                    if current is None:
                        await database.execute(
                            "INSERT INTO health_status_history ("
                            "event_id, category, name, previous_status, "
                            "status, "
                            "changed_at, is_current"
                            ") VALUES ("
                            ":event_id, :category, :name, NULL, :status, "
                            ":checked_at, 1"
                            ")",
                            {
                                **item,
                                "event_id": _new_event_id(),
                                "checked_at": checked_at,
                            },
                        )
                        continue

                    previous_status = str(current["status"] or "unknown")
                    if previous_status == item["status"]:
                        continue

                    await database.execute(
                        "UPDATE health_status_history SET is_current = 0 "
                        "WHERE event_id = :event_id AND is_current = 1",
                        {"event_id": current["event_id"]},
                    )
                    await database.execute(
                        "INSERT INTO health_status_history ("
                        "event_id, category, name, previous_status, status, "
                        "changed_at, is_current"
                        ") VALUES ("
                        ":event_id, :category, :name, :previous_status, "
                        ":status, "
                        ":checked_at, 1"
                        ")",
                        {
                            **item,
                            "event_id": _new_event_id(),
                            "previous_status": previous_status,
                            "checked_at": checked_at,
                        },
                    )

            await run_database_transaction(
                write_snapshot,
                "record health-status history",
            )
    except Exception as error:
        database_logger.warning(
            "[HealthHistory] Snapshot not stored: "
            f"{type(error).__name__}: {error}"
        )


# ===========================
# Health History Retrieval
# ===========================
def get_health_history_retention_seconds() -> int:
    return max(1, int(settings.HEALTH_STATUS_HISTORY_RETENTION))


def _parse_history_cursor(
    cursor: Optional[str],
) -> Optional[Tuple[int, str]]:
    if cursor is None:
        return None
    if not cursor or len(cursor) > HEALTH_HISTORY_CURSOR_MAX_LENGTH:
        raise ValueError("Invalid health-history cursor")

    changed_at_text, separator, event_id = cursor.partition(":")
    if (
        not separator
        or not changed_at_text.isdigit()
        or not event_id
        or len(event_id) > HEALTH_HISTORY_CURSOR_MAX_LENGTH
    ):
        raise ValueError("Invalid health-history cursor")

    changed_at = int(changed_at_text)
    if changed_at <= 0 or changed_at > _MAX_HISTORY_TIMESTAMP:
        raise ValueError("Invalid health-history cursor")
    return changed_at, event_id


def _build_history_cursor(row: Any) -> str:
    return f"{int(row['changed_at'])}:{row['event_id']}"


async def get_health_history_page(
    limit: int = HEALTH_HISTORY_PAGE_SIZE,
    cursor: Optional[str] = None,
) -> Dict[str, Any]:
    safe_limit = min(HEALTH_HISTORY_MAX_PAGE_SIZE, max(1, int(limit)))
    cursor_values = _parse_history_cursor(cursor)
    cutoff = int(time.time()) - get_health_history_retention_seconds()
    cursor_clause = ""
    parameters = {
        "cutoff": cutoff,
        "offline_status": "offline",
        "online_status": "online",
        "fetch_limit": safe_limit + 1,
    }
    if cursor_values is not None:
        cursor_clause = (
            "AND (changed_at < :cursor_changed_at OR ("
            "changed_at = :cursor_changed_at "
            "AND event_id < :cursor_event_id)) "
        )
        parameters.update({
            "cursor_changed_at": cursor_values[0],
            "cursor_event_id": cursor_values[1],
        })

    try:
        rows = await database.fetch_all(
            "SELECT event_id, category, name, previous_status, status, "
            "changed_at "
            "FROM health_status_history "
            "WHERE changed_at >= :cutoff "
            "AND (status = :offline_status OR ("
            "previous_status = :offline_status AND status = :online_status"
            ")) "
            f"{cursor_clause}"
            "ORDER BY changed_at DESC, event_id DESC LIMIT :fetch_limit",
            parameters,
        )
    except Exception as error:
        database_logger.warning(
            "[HealthHistory] History not loaded: "
            f"{type(error).__name__}: {error}"
        )
        return {
            "events": [],
            "has_more": False,
            "next_cursor": None,
            "available": False,
        }

    has_more = len(rows) > safe_limit
    visible_rows = rows[:safe_limit]
    events = []
    for row in visible_rows:
        status = str(row["status"])
        events.append({
            "category": str(row["category"]),
            "name": str(row["name"]),
            "event": "outage" if status == "offline" else "recovery",
            "changed_at": int(row["changed_at"]),
        })
    return {
        "events": events,
        "has_more": has_more,
        "next_cursor": (
            _build_history_cursor(visible_rows[-1])
            if has_more and visible_rows
            else None
        ),
        "available": True,
    }
