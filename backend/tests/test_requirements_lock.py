"""requirements.in and requirements.txt must agree.

`build.sh` installs from requirements.txt. On 2026-10-01 a fresh build on the
MBP shipped WITHOUT `mnemonic` (recovery phrases — the Encrypt banner's first
step failed) and WITHOUT `fastmcp` (the whole /mcp server Jocasta uses): both had
been added to requirements.in and installed by hand on the dev Mac, so every test
there passed while the lock file never named them.
"""
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def _names(path, top_level_only=False):
    out = set()
    for line in (BACKEND / path).read_text().splitlines():
        line = line.split("#")[0].strip()
        if not line or line.startswith("-"):
            continue
        out.add(re.split(r"[<>=!~\[; ]", line)[0].lower().replace("_", "-"))
    return out


def test_every_requirement_is_pinned_in_the_lock_file():
    missing = sorted(_names("requirements.in") - _names("requirements.txt"))
    assert missing == [], f"in requirements.in but not pinned in requirements.txt: {missing}"
