"""Minimal test scaffolding so the suite runs with plain `python`, no pytest.

Importing this module also puts the repo root on sys.path, so test files can do
`import bot.integrations.twitch_api` regardless of where they're run from.
"""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# The code under test prints emoji. Windows consoles default to cp1252 and would
# raise UnicodeEncodeError mid-test, so swap unencodable characters instead of
# crashing. Without this you'd need PYTHONIOENCODING=utf-8 on every run.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# TwitchAPI refuses to construct without these. The values are never sent anywhere;
# every test replaces the HTTP session with a fake.
os.environ.setdefault("TWITCH_CLIENT_ID", "test-client-id")
os.environ.setdefault("TWITCH_CLIENT_SECRET", "test-client-secret")


class Suite:
    """Collects pass/fail results and prints them as it goes."""

    def __init__(self, title: str):
        self.title = title
        self.passed = 0
        self.failed = 0
        print(f"\n{title}")
        print("-" * len(title))

    def check(self, name: str, condition, detail: str = "") -> bool:
        ok = bool(condition)
        if ok:
            self.passed += 1
        else:
            self.failed += 1
        line = f"  {'PASS' if ok else 'FAIL'}  {name:<46}"
        if detail:
            line += f" {detail}"
        print(line)
        return ok

    def summary(self) -> bool:
        """Print the tally and return True if everything passed."""
        total = self.passed + self.failed
        print(f"  {self.passed}/{total} passed")
        return self.failed == 0
