import hashlib
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Dict, Optional

from wastream.utils.database import (
    DEAD_LINK_STATUS_CONFIRMED,
    DEAD_LINK_STATUS_RECHECKABLE,
    SearchLock,
    clear_recheckable_dead_link_state,
    confirm_recheckable_dead_link_state,
    get_dead_link_status,
    record_dead_link_failure,
)
from wastream.utils.logger import debrid_logger
from wastream.utils.urls import canonicalize_url


# ===========================
# Result Classification
# ===========================
DEAD_LINK_FAILURE_RESULTS = frozenset({"LINK_DOWN"})
PLAYBACK_SENTINELS = DEAD_LINK_FAILURE_RESULTS | frozenset({
    "LINK_SERVICE_DOWN",
    "LINK_UNSUPPORTED",
    "LINK_UNCACHED",
    "RETRY_ERROR",
    "FATAL_ERROR",
})


# ===========================
# Configuration
# ===========================
def is_dead_link_recheck_enabled(config: Optional[Dict]) -> bool:
    config = config or {}
    enabled = config.get("recheck_dead_links")
    if enabled is not None:
        if isinstance(enabled, bool):
            return enabled
        return str(enabled).strip().lower() in {"1", "true", "yes", "on"}

    return str(config.get("dead_link_recheck_mode", "")).strip().lower() == "manual"


# ===========================
# Playback Guard State
# ===========================
@dataclass(frozen=True)
class PlaybackGuard:
    blocked_result: Optional[str] = None
    is_recheck: bool = False
    recheck_started_at: int = 0


# ===========================
# Dead Link Recheck Service
# ===========================
class DeadLinkRecheckService:
    @staticmethod
    def _link_lock_identity(link: str) -> str:
        return hashlib.sha256(link.encode()).hexdigest()

    @asynccontextmanager
    async def guard_playback(
        self,
        link: str,
        config: Dict,
    ):
        canonical_link = canonicalize_url(link) or link
        state = await get_dead_link_status(canonical_link)
        failure_count = int(state.get("failure_count", 0))

        if failure_count >= DEAD_LINK_STATUS_CONFIRMED:
            yield PlaybackGuard("LINK_DOWN")
            return

        if failure_count != DEAD_LINK_STATUS_RECHECKABLE:
            yield PlaybackGuard()
            return

        if not is_dead_link_recheck_enabled(config):
            yield PlaybackGuard("LINK_DOWN")
            return

        async with SearchLock(
            "dead_link_recheck",
            self._link_lock_identity(canonical_link),
            wait=False,
        ) as recheck_lock:
            if not recheck_lock.acquired:
                yield PlaybackGuard("RETRY_ERROR")
                return

            state = await get_dead_link_status(canonical_link)
            failure_count = int(state.get("failure_count", 0))
            if failure_count >= DEAD_LINK_STATUS_CONFIRMED:
                yield PlaybackGuard("LINK_DOWN")
                return
            if failure_count != DEAD_LINK_STATUS_RECHECKABLE:
                yield PlaybackGuard()
                return

            yield PlaybackGuard(
                is_recheck=True,
                recheck_started_at=int(state.get("first_failure_at", 0)),
            )

    async def record_playback_result(
        self,
        link: str,
        result: Optional[str],
        record_failure: bool = True,
        is_recheck: bool = False,
        recheck_started_at: int = 0,
    ) -> None:
        try:
            if is_recheck and result and result not in PLAYBACK_SENTINELS:
                await clear_recheckable_dead_link_state(
                    link,
                    recheck_started_at,
                )
                return

            if (
                result not in DEAD_LINK_FAILURE_RESULTS
                or not record_failure
            ):
                return

            if is_recheck:
                await confirm_recheckable_dead_link_state(
                    link,
                    recheck_started_at,
                    result,
                )
                return

            await record_dead_link_failure(
                link,
                result,
            )
        except Exception as error:
            debrid_logger.error(
                "[DeadLinkRecheck] Failed to update state: "
                f"{type(error).__name__}: {error}"
            )


# ===========================
# Singleton Instance
# ===========================
dead_link_recheck_service = DeadLinkRecheckService()
