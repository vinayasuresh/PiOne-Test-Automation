import sys
from pathlib import Path

from loguru import logger

from automation_framework.config.settings import LOG_PATH
from automation_framework.utils.redactor import redact_text


LOG_PATH.mkdir(parents=True, exist_ok=True)

logger.remove()

# Patch loguru so any message logged anywhere is automatically redacted of credentials and tokens
logger = logger.patch(lambda record: record.update(message=redact_text(str(record["message"]))))

logger.add(
    sys.stderr,
    level="INFO",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}",
)


def configure_run_logger(run_id: str) -> Path:
    """Attach a per-run file sink so each execution produces its own log file."""
    # Sanitize run_id to avoid path traversal in logger
    safe_run_id = "".join(c for c in run_id if c.isalnum() or c in ("-", "_"))
    log_file = LOG_PATH / f"{safe_run_id}.log"

    logger.add(
        log_file,
        level="INFO",
        enqueue=True,
        backtrace=False,
        diagnose=False,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {name}:{function}:{line} | {message}",
    )

    return log_file
