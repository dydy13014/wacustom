import re
import sys
import logging
from collections import deque
from contextvars import ContextVar
from threading import Lock
from typing import List, Dict, Optional
from loguru import logger


PREFIX_PATTERN = re.compile(r'^\[([^\]]+)\]')

user_id_var: ContextVar[Optional[str]] = ContextVar("user_id", default=None)


LOG_LEVEL = "INFO"
MAX_LOG_BUFFER = 5000

CONTEXTS = {
    "ADDON": {"color": "green", "icon": "🚀", "hex": "#22c55e"},
    "API": {"color": "cyan", "icon": "🔗", "hex": "#06b6d4"},
    "STREAM": {"color": "yellow", "icon": "🎬", "hex": "#eab308"},
    "SCRAPER": {"color": "blue", "icon": "🌐", "hex": "#3b82f6"},
    "DEBRID": {"color": "magenta", "icon": "☁️", "hex": "#a855f7"},
    "METADATA": {"color": "white", "icon": "🎭", "hex": "#f5f5f5"},
    "CACHE": {"color": "white", "icon": "💾", "hex": "#f5f5f5"},
    "DATABASE": {"color": "yellow", "icon": "🗄️", "hex": "#eab308"},
    "REMOTE": {"color": "red", "icon": "🔄", "hex": "#ef4444"},
}

LEVEL_ICONS = {
    "DEBUG": "🔍",
    "INFO": "ℹ️ ",
    "WARNING": "⚠️",
    "ERROR": "❌",
}

LEVEL_COLORS = {
    "DEBUG": "#71717a",
    "INFO": "#3b82f6",
    "WARNING": "#f59e0b",
    "ERROR": "#ef4444",
}


class LogBuffer:
    def __init__(self, max_size: int = MAX_LOG_BUFFER):
        self._buffer: deque = deque(maxlen=max_size)
        self._lock = Lock()

    def add(self, log_entry: Dict):
        with self._lock:
            self._buffer.append(log_entry)

    def get_logs(self, since: float = 0, context: Optional[str] = None, level: Optional[str] = None, prefix: Optional[str] = None, search: Optional[str] = None, user_id: Optional[str] = None) -> List[Dict]:
        with self._lock:
            logs = list(self._buffer)

        if since > 0:
            logs = [log for log in logs if log["timestamp"] > since]

        if context:
            logs = [log for log in logs if log["context"] == context]

        if level:
            level_priority = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3}
            min_priority = level_priority.get(level.upper(), 0)
            logs = [log for log in logs if level_priority.get(log["level"], 0) >= min_priority]

        if prefix:
            if prefix == "__none__":
                logs = [log for log in logs if not PREFIX_PATTERN.match(log["message"])]
            else:
                logs = [log for log in logs if log["message"].startswith(f"[{prefix}]")]

        if user_id:
            user_id_lower = user_id.lower()
            logs = [log for log in logs if log.get("user_id") and user_id_lower in log["user_id"].lower()]

        if search:
            search_lower = search.lower()
            logs = [
                log for log in logs
                if search_lower in log["message"].lower()
                or (log.get("user_id") and search_lower in log["user_id"].lower())
            ]

        return logs

    def get_available_prefixes(self) -> List[str]:
        with self._lock:
            logs = list(self._buffer)

        prefixes = set()
        for log in logs:
            match = PREFIX_PATTERN.match(log["message"])
            if match:
                prefixes.add(match.group(1))

        return sorted(prefixes)

    def clear(self):
        with self._lock:
            self._buffer.clear()


log_buffer = LogBuffer()


def log_sink(message):
    record = message.record
    context = record["extra"].get("context", "ADDON")
    context_data = CONTEXTS.get(context, {"icon": "📦", "hex": "#71717a"})
    level_name = record["level"].name
    user_id = user_id_var.get()

    log_entry = {
        "timestamp": record["time"].timestamp(),
        "time": record["time"].strftime("%Y-%m-%d %H:%M:%S"),
        "level": level_name,
        "level_icon": LEVEL_ICONS.get(level_name, ""),
        "level_color": LEVEL_COLORS.get(level_name, "#71717a"),
        "context": context,
        "context_icon": context_data["icon"],
        "context_color": context_data["hex"],
        "message": record["message"],
        "user_id": user_id,
    }

    log_buffer.add(log_entry)


def format_log(record):
    context = record["extra"].get("context", "ADDON")
    context_data = CONTEXTS.get(context, {"color": "white", "icon": "📦"})
    context_color = context_data["color"]
    context_icon = context_data["icon"]
    level_icon = LEVEL_ICONS.get(record["level"].name, "")

    user_id = user_id_var.get()
    user_segment = f"<yellow>👤 {user_id[:8]}</yellow> | " if user_id else ""

    return (
        "<white>{time:YYYY-MM-DD}</white> "
        "<magenta>{time:HH:mm:ss}</magenta> | "
        f"<level>{level_icon} {{level: <8}}</level> | "
        f"<{context_color}>{context_icon} {{extra[context]: <10}}</{context_color}> | "
        f"{user_segment}"
        "<level>{message}</level>\n"
    )


def setup_logger(level: str = "INFO"):
    global LOG_LEVEL
    LOG_LEVEL = level

    logger.remove()

    logger.add(
        sys.stderr,
        level=LOG_LEVEL,
        format=format_log,
        colorize=True,
        backtrace=True,
        diagnose=True,
    )

    logger.add(
        log_sink,
        level="DEBUG",
        format="{message}",
    )


def get_logger(context: str):
    return logger.bind(context=context)


def get_logs(since: float = 0, context: Optional[str] = None, level: Optional[str] = None, prefix: Optional[str] = None, search: Optional[str] = None, user_id: Optional[str] = None) -> List[Dict]:
    return log_buffer.get_logs(since, context, level, prefix, search, user_id)


def clear_logs():
    log_buffer.clear()


def get_available_contexts() -> List[str]:
    return list(CONTEXTS.keys())


def get_available_prefixes() -> List[str]:
    return log_buffer.get_available_prefixes()


addon_logger = get_logger("ADDON")
api_logger = get_logger("API")
stream_logger = get_logger("STREAM")
scraper_logger = get_logger("SCRAPER")
debrid_logger = get_logger("DEBRID")
metadata_logger = get_logger("METADATA")
cache_logger = get_logger("CACHE")
database_logger = get_logger("DATABASE")
remote_logger = get_logger("REMOTE")

logging.getLogger("uvicorn.access").disabled = True
logging.getLogger("uvicorn.error").setLevel(logging.CRITICAL)
logging.getLogger("fastapi").setLevel(logging.CRITICAL)
