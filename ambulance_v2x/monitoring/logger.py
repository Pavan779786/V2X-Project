"""
Structured Logging Module for Ambulance V2X
Provides multi-target logging:
- Console handler with Colorama coloring
- Rotating file handler for app.log (general application events)
- Rotating file handler for errors.log (warnings, errors, criticals)
- Rotating JSON file handler for messages.log (structured V2X transmission audit)
"""

import os
import sys
import json
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone
from typing import Any, Dict, Optional

try:
    import colorama
    from colorama import Fore, Style
    colorama.init(autoreset=True)
    HAS_COLORAMA = True
except ImportError:
    HAS_COLORAMA = False

from config import APP_LOG_PATH, MESSAGES_LOG_PATH, ERRORS_LOG_PATH


class ColoredConsoleFormatter(logging.Formatter):
    """Console formatter with color highlights based on log level."""

    COLORS = {
        logging.DEBUG: Fore.CYAN if HAS_COLORAMA else "",
        logging.INFO: Fore.GREEN if HAS_COLORAMA else "",
        logging.WARNING: Fore.YELLOW if HAS_COLORAMA else "",
        logging.ERROR: Fore.RED if HAS_COLORAMA else "",
        logging.CRITICAL: Fore.MAGENTA + Style.BRIGHT if HAS_COLORAMA else "",
    }
    RESET = Style.RESET_ALL if HAS_COLORAMA else ""

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelno, "")
        timestamp = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        levelname = f"[{record.levelname:^8}]"
        name = f"[{record.name}]"
        message = record.getMessage()
        return f"{color}{timestamp} {levelname} {name:<20} {message}{self.RESET}"


class JSONLogFormatter(logging.Formatter):
    """Formats log records as JSON lines for automated parsing and analytics."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        if hasattr(record, "extra_data") and isinstance(record.extra_data, dict):
            log_entry.update(record.extra_data)
        return json.dumps(log_entry)


# Dedicated logger for V2X protocol message traces
_message_logger: Optional[logging.Logger] = None


def setup_logging(level: int = logging.INFO, enable_console: bool = True) -> None:
    """Initialize root and module-specific loggers with rotating file handlers."""
    global _message_logger

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # Clear existing handlers to avoid duplicates
    root_logger.handlers.clear()

    # 1. Console Handler
    if enable_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(ColoredConsoleFormatter())
        root_logger.addHandler(console_handler)

    # 2. General App Log (Rotating, 10MB x 5 backups)
    app_handler = RotatingFileHandler(
        APP_LOG_PATH, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    app_handler.setLevel(logging.DEBUG)
    app_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] [%(name)s:%(lineno)d] %(message)s")
    )
    root_logger.addHandler(app_handler)

    # 3. Error Log (Warnings and Errors only)
    error_handler = RotatingFileHandler(
        ERRORS_LOG_PATH, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    error_handler.setLevel(logging.WARNING)
    error_handler.setFormatter(JSONLogFormatter())
    root_logger.addHandler(error_handler)

    # 4. Message Audit Logger (messages.log)
    _message_logger = logging.getLogger("v2x.messages")
    _message_logger.setLevel(logging.INFO)
    _message_logger.propagate = False
    _message_logger.handlers.clear()

    msg_file_handler = RotatingFileHandler(
        MESSAGES_LOG_PATH, maxBytes=25 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    msg_file_handler.setLevel(logging.INFO)
    msg_file_handler.setFormatter(logging.Formatter("%(message)s"))
    _message_logger.addHandler(msg_file_handler)


def get_logger(name: str) -> logging.Logger:
    """Get or create a named logger within the v2x namespace."""
    if not name.startswith("v2x"):
        name = f"v2x.{name}"
    return logging.getLogger(name)


def log_v2x_message(
    msg_type: str,
    station_id: int,
    seq_num: int,
    payload: Dict[str, Any],
    transport: str = "UDP",
    status: str = "SENT",
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Log an outgoing or incoming V2X message in JSON lines format to messages.log."""
    if _message_logger is None:
        setup_logging()

    record: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "msg_type": msg_type,
        "station_id": station_id,
        "seq_num": seq_num,
        "transport": transport,
        "status": status,
        "payload": payload,
    }
    if extra:
        record["meta"] = extra

    if _message_logger:
        _message_logger.info(json.dumps(record))
