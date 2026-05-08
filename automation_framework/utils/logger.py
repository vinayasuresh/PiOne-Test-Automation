import sys
from pathlib import Path

from loguru import logger

from automation_framework.config.settings import LOG_PATH


LOG_PATH.mkdir(parents=True, exist_ok=True)

logger.remove()

logger.add(
    sys.stderr,
    level="INFO",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}",
)


def configure_run_logger(run_id: str) -> Path:
    """Attach a per-run file sink so each execution produces its own log file."""
    log_file = LOG_PATH / f"{run_id}.log"

    logger.add(
        log_file,
        level="INFO",
        enqueue=True,
        backtrace=False,
        diagnose=False,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {name}:{function}:{line} | {message}",
    )

    return log_file

