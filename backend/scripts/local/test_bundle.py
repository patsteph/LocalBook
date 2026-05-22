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


def fetch(base_url: str, path: str, timeout: int = 10, expect_status: int = 200) -> dict:
    """GET {base_url}{path}, return parsed JSON. Raises RuntimeError on any failure."""
    url = f"{base_url}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
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
            data = fetch(base, path, timeout=args.timeout)
            err = validator(data) if validator else None
            if err:
                print(f"  FAIL  {label:<22} ({path}): {err}")
                failures += 1
            else:
                dt_ms = int((time.time() - t0) * 1000)
                print(f"  OK    {label:<22} ({path})  {dt_ms}ms")
        except Exception as e:
            print(f"  FAIL  {label:<22} ({path}): {e}")
            failures += 1

    print()
    if failures:
        print(f"Bundle smoke test FAILED — {failures} check(s) failed")
        return 1
    print("Bundle smoke test PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
