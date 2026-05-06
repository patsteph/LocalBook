"""Centralized logging configuration using Rich for readable terminal output.

Call setup_logging() once at startup (before other imports that use logging).
This installs:
  - Rich tracebacks (syntax-highlighted, shows locals on error)
  - RichHandler on the root logger (colored, timestamped log lines)
  - Rotating file handler at ~/Library/Logs/LocalBook/backend.log so the
    bundled-binary process (whose stdout the Tauri app swallows) leaves
    a tailable trail for support / live debugging.
  - Suppresses noisy third-party loggers
"""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def _resolve_log_path() -> Path:
    """Where should backend.log live?

    macOS  →  ~/Library/Logs/LocalBook/backend.log (Apple HIG location;
              shows up in Console.app under "Log Reports").
    Linux  →  ~/.local/state/LocalBook/backend.log (XDG state dir).
    Other  →  ~/.localbook/backend.log

    Honors LOCALBOOK_LOG_DIR override for tests / packaged builds that
    want logs alongside the .app bundle.
    """
    override = os.environ.get("LOCALBOOK_LOG_DIR")
    if override:
        base = Path(override).expanduser()
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Logs" / "LocalBook"
    elif sys.platform.startswith("linux"):
        base = Path(
            os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))
        ) / "LocalBook"
    else:
        base = Path.home() / ".localbook"
    base.mkdir(parents=True, exist_ok=True)
    return base / "backend.log"


def setup_logging(level: int = logging.INFO) -> None:
    """Configure the root logger with Rich output AND a rotating log file.

    Safe to call in PyInstaller bundles — falls back to standard
    logging if Rich is unavailable (should never happen since Rich
    is already a transitive dependency). The file handler is best-effort
    too: if the log directory can't be created (read-only home, weird
    sandboxing), we skip it without breaking startup.
    """
    handlers: list[logging.Handler] = []

    # ── Console: Rich if available, else stdlib basicConfig ──
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

        handlers.append(
            RichHandler(
                level=level,
                rich_tracebacks=True,
                tracebacks_show_locals=True,
                markup=False,
                show_path=False,
                log_time_format="[%H:%M:%S]",
            )
        )
        console_format = "%(message)s"
    except ImportError:
        console_format = "[%(asctime)s] %(levelname)-7s %(name)s — %(message)s"
        handlers.append(logging.StreamHandler())

    # ── File handler: tailable log for support & debugging ──
    # Critical because the Tauri app launches the backend with its
    # stdout/stderr inherited but no terminal attached when launched
    # from Finder, so console output is invisible. Without this file
    # there is no way to see what the running backend is doing.
    try:
        log_path = _resolve_log_path()
        file_handler = RotatingFileHandler(
            str(log_path),
            maxBytes=10 * 1024 * 1024,   # 10 MB per file
            backupCount=3,                # keep 3 historical logs (~40 MB max)
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(
            logging.Formatter(
                fmt="[%(asctime)s] %(levelname)-7s %(name)s — %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        handlers.append(file_handler)
    except Exception as e:  # pragma: no cover — never break startup over logging
        # Use stderr directly since logging isn't configured yet.
        print(f"[logging] Could not set up file handler: {e}", file=sys.stderr)

    logging.basicConfig(
        level=level,
        format=console_format,
        datefmt="[%H:%M:%S]",
        handlers=handlers,
        force=True,
    )

    # One-line breadcrumb so we can confirm at a glance which run a
    # given log file belongs to.
    try:
        log_path = _resolve_log_path()
        logging.getLogger(__name__).info(
            f"Logging initialized → console + {log_path} (PID {os.getpid()})"
        )
    except Exception:
        pass

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
