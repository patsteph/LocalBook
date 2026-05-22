"""
Path-safety helpers — protect user-supplied paths from traversal and sensitive-target reads.

P0.2 (2026-05-15).

Two helpers:

- ``strict_path_under_base(base, user_supplied)`` — for fixed-directory file serving.
  Accepts only paths under the given base. Used for serving generated artifacts
  (e.g., audio files out of a single output directory).

- ``is_safe_user_path(path_str)`` — for endpoints where the user legitimately picks
  files from anywhere via a file picker. Accepts paths under ``$HOME``, ``/Volumes``,
  or ``settings.data_dir`` but rejects:
    - System directories: ``/etc``, ``/var``, ``/usr``, ``/System``, ``/private``,
      ``/dev``, ``/sbin``, ``/bin``
    - Dotfile config directories inside ``$HOME`` (``.ssh``, ``.aws``, etc.)
    - macOS credential/mail/messages stores under ``~/Library``

The validation is defense-in-depth. Once token auth lands (P0.1), only the Tauri
webview and the paired extension can reach these endpoints — but the path check
stays in place as belt + suspenders.
"""
from pathlib import Path
from typing import Tuple

from fastapi import HTTPException


# Hard denylist of system roots. Any path that resolves under one of these is
# refused regardless of other rules.
_FORBIDDEN_SYSTEM_ROOTS: Tuple[str, ...] = (
    "/etc",
    "/var",
    "/usr",
    "/System",
    "/private",
    "/dev",
    "/sbin",
    "/bin",
)

# Sensitive subdirs inside $HOME — mostly dotfiles holding credentials.
_SENSITIVE_HOME_SUBDIRS: Tuple[str, ...] = (
    ".ssh",
    ".aws",
    ".config",
    ".gnupg",
    ".docker",
    ".kube",
    ".npm",
    ".pip",
    ".cargo",
    ".rustup",
    ".password-store",
    ".gem",
)

# Sensitive subdirs inside ~/Library — macOS-specific credential/mail stores.
_SENSITIVE_LIBRARY_SUBDIRS: Tuple[str, ...] = (
    "Keychains",
    "Cookies",
    "Mail",
    "Messages",
    "PasswordVault",
    "Calendars",
    "Contacts",
    "Reminders",
)


def strict_path_under_base(base: Path, user_supplied: str) -> Path:
    """Resolve ``user_supplied`` relative to ``base``; raise HTTPException(400)
    if the resolved path escapes ``base``. Returns the resolved Path on success.
    """
    base_resolved = base.resolve()
    try:
        target = (base_resolved / user_supplied).resolve()
        target.relative_to(base_resolved)
    except (ValueError, OSError):
        raise HTTPException(status_code=400, detail="Invalid path")
    return target


def _is_under(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def is_safe_user_path(path_str: str, *, allow_data_dir: bool = True) -> Tuple[bool, str]:
    """Return ``(is_safe, reason)``. Reason is empty when safe.

    Acceptance rules:
        - Path must be absolute (after ``expanduser`` + ``resolve``).
        - Path must resolve under one of: ``$HOME``, ``/Volumes``, optionally
          ``settings.data_dir``.
        - Path must not be under any system root (``/etc``, ``/var``, ...).
        - Path must not be under any sensitive subdirectory inside ``$HOME``
          (``.ssh``, ``.aws``, ...) or ``~/Library`` (``Keychains``, ``Mail``, ...).

    Resolution uses ``strict=False`` so the helper works before a file exists.
    Callers still check ``.exists()`` separately when relevant.
    """
    # Check absoluteness on the raw input — resolve() would make every path
    # absolute by joining with cwd, hiding relative-path callers.
    raw = Path(path_str).expanduser()
    if not raw.is_absolute():
        return False, "path must be absolute"

    try:
        p = raw.resolve(strict=False)
    except Exception as e:
        return False, f"path could not be resolved: {e}"

    # System-directory denylist (highest priority).
    p_str = str(p)
    for root in _FORBIDDEN_SYSTEM_ROOTS:
        if p_str == root or p_str.startswith(root + "/"):
            return False, f"path is under system directory {root}"

    home = Path.home().resolve()

    # Allowed roots.
    allowed_roots = [home, Path("/Volumes")]
    if allow_data_dir:
        try:
            from config import settings
            allowed_roots.append(Path(settings.data_dir).resolve())
        except Exception:
            # If config is unavailable, just skip the data_dir allowance.
            pass

    if not any(_is_under(p, root) for root in allowed_roots):
        return False, "path is outside allowed roots (must be under home, /Volumes, or app data)"

    # Sensitive subpaths inside $HOME.
    for sub in _SENSITIVE_HOME_SUBDIRS:
        if _is_under(p, home / sub):
            return False, f"path is in sensitive directory ~/{sub}"

    library = home / "Library"
    for sub in _SENSITIVE_LIBRARY_SUBDIRS:
        if _is_under(p, library / sub):
            return False, f"path is in sensitive directory ~/Library/{sub}"

    return True, ""


def require_safe_user_path(path_str: str, *, allow_data_dir: bool = True) -> Path:
    """Convenience wrapper for FastAPI handlers: raise HTTPException(400) if
    the path is unsafe; otherwise return the resolved ``Path``.
    """
    ok, reason = is_safe_user_path(path_str, allow_data_dir=allow_data_dir)
    if not ok:
        raise HTTPException(status_code=400, detail=f"Invalid file_path: {reason}")
    return Path(path_str).expanduser().resolve(strict=False)
