"""Run every MessiahBot test suite.

No pytest, no network, no database, no credentials — just:

    python tests/run_tests.py

Exits 0 if everything passed, 1 otherwise (so CI can use it as-is later).
"""

import sys
from pathlib import Path

# Let the suites import each other and the `bot` package by name.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_token_refresh  # noqa: E402
import test_twitch_api  # noqa: E402

SUITES = (
    ("Twitch API / rate limiting", test_twitch_api.main),
    ("Twitch token refresh", test_token_refresh.main),
)


def main() -> int:
    failed = [name for name, run in SUITES if not run()]

    print()
    if failed:
        print(f"❌ {len(failed)} of {len(SUITES)} suites failed:")
        for name in failed:
            print(f"   - {name}")
        return 1

    print(f"✅ All {len(SUITES)} suites passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
