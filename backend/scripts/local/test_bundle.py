#!/usr/bin/env python3
"""
Bundle smoke test — verifies the running .app's backend responds to critical endpoints.

Used by release.sh step 6 to confirm a fresh build is functional before publishing.
Can also be run manually against any running backend for a quick sanity check.

Exit 0 on success, non-zero on the first failure with a clear message.

Usage:
    python3 backend/scripts/local/test_bundle.py
    python3 backend/scripts/local/test_bundle.py --base-url http://localhost:8000
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


# Per-launch app token (see backend/utils/auth_middleware.py). All non-exempt
# endpoints require X-LocalBook-Token. We read it from the same file the
# backend writes at startup so this smoke test can authenticate.
TOKEN_PATH = Path.home() / "Library" / "Application Support" / "LocalBook" / ".app_token"


def load_app_token() -> str | None:
    """Return the running backend's app token, or None if the file is missing/empty."""
    try:
        token = TOKEN_PATH.read_text().strip()
        return token or None
    except OSError:
        return None


def fetch(base_url: str, path: str, timeout: int = 10, expect_status: int = 200,
          app_token: str | None = None) -> dict:
    """GET {base_url}{path}, return parsed JSON. Raises RuntimeError on any failure."""
    url = f"{base_url}{path}"
    req = urllib.request.Request(url)
    if app_token:
        req.add_header("X-LocalBook-Token", app_token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != expect_status:
                raise RuntimeError(f"status {resp.status}, expected {expect_status}")
            raw = resp.read()
            if not raw:
                return {}
            try:
                return json.loads(raw)
            except json.JSONDecodeError as e:
                raise RuntimeError(f"response is not valid JSON: {e}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} {e.reason}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"connection error: {e.reason}")
    except (TimeoutError, OSError) as e:
        raise RuntimeError(f"network error: {e}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Bundle smoke test for LocalBook backend")
    parser.add_argument("--base-url", default="http://localhost:8000",
                        help="Backend URL (default: http://localhost:8000)")
    parser.add_argument("--timeout", type=int, default=10,
                        help="Per-request timeout in seconds (default: 10)")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    print(f"Bundle smoke test → {base}")

    app_token = load_app_token()
    if app_token:
        print(f"  [auth] using app token from {TOKEN_PATH.name} ({len(app_token)} chars)")
    else:
        print(f"  [auth] no app token at {TOKEN_PATH} — auth-protected endpoints will 401")

    # Each check: (label, path, optional response validator)
    # Validators receive the parsed JSON and return None on success or an error string.
    checks = [
        ("Health endpoint",      "/health",
         lambda d: None if d.get("status") == "healthy" else f"expected status=healthy, got {d.get('status')!r}"),
        ("Notebooks list",       "/notebooks/",
         lambda d: None if isinstance(d, dict) and isinstance(d.get("notebooks"), list)
                   else f"expected dict with 'notebooks' list, got {type(d).__name__}"),
        ("Settings preferences", "/settings/preferences",
         lambda d: None if isinstance(d, dict) else f"expected dict, got {type(d).__name__}"),
        ("Skills list",          "/skills/",
         lambda d: None if isinstance(d, list) else f"expected list, got {type(d).__name__}"),
        ("Updates version",      "/updates/version",
         lambda d: None if isinstance(d, dict) else f"expected dict, got {type(d).__name__}"),
    ]

    failures = 0
    for label, path, validator in checks:
        t0 = time.time()
        try:
            data = fetch(base, path, timeout=args.timeout, app_token=app_token)
            err = validator(data) if validator else None
            if err:
                print(f"  [FAIL] {label:<22} ({path}): {err}")
                failures += 1
            else:
                dt_ms = int((time.time() - t0) * 1000)
                print(f"  [OK]   {label:<22} ({path})  {dt_ms}ms")
        except Exception as e:
            print(f"  [FAIL] {label:<22} ({path}): {e}")
            failures += 1

    print()
    if failures:
        # Wording matched to release.sh's filter / failure detection.
        print(f"Bundle verification FAILED — Failed: {failures} check(s)")
        return 1
    # Wording matched to release.sh's success-string check.
    print(f"Bundle verification passed — Passed: {len(checks)} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
