"""Centralized logging configuration using Rich for readable terminal output.

Call setup_logging() once at startup (before other imports that use logging).
This installs:
  - Rich tracebacks (syntax-highlighted, shows locals on error)
  - RichHandler on the root logger (colored, timestamped log lines)
  - Suppresses noisy third-party loggers
"""
import logging


def setup_logging(level: int = logging.INFO) -> None:
    """Configure the root logger with Rich output.

    Safe to call in PyInstaller bundles — falls back to standard
    logging if Rich is unavailable (should never happen since Rich
    is already a transitive dependency).
    """
    try:
        from rich.logging import RichHandler
        from rich.traceback import install as install_traceback

        install_traceback(
            show_locals=True,
            width=120,
            suppress=[
                "uvicorn",
                "starlette",
                "fastapi",
                "httpx",
                "anyio",
                "asyncio",
            ],
        )

        handler = RichHandler(
            level=level,
            rich_tracebacks=True,
            tracebacks_show_locals=True,
            markup=False,
            show_path=False,
            log_time_format="[%H:%M:%S]",
        )

        logging.basicConfig(
            level=level,
            format="%(message)s",
            datefmt="[%H:%M:%S]",
            handlers=[handler],
            force=True,
        )

    except ImportError:
        logging.basicConfig(
            level=level,
            format="[%(asctime)s] %(levelname)-7s %(name)s — %(message)s",
            datefmt="%H:%M:%S",
            force=True,
        )

    _quiet_noisy_loggers()


def _quiet_noisy_loggers() -> None:
    """Suppress chatty third-party loggers that clutter output."""
    for name in [
        "httpx",
        "httpcore",
        "uvicorn.access",
        "uvicorn.error",
        "watchfiles",
        "sentence_transformers",
        "transformers",
        "torch",
        "opentelemetry",
        "hpack",
        "multipart",
        "bertopic",
    ]:
        logging.getLogger(name).setLevel(logging.WARNING)
