import argparse
import json

from athlete_os.services.intervals_client import get_recent_wellness_normalized
from athlete_os.services.journal_store import migrate_legacy_context_notes


DEFAULT_DAYS = 3650
MAX_DAYS = 3650


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate legacy Intervals context notes into Athlete OS Journal."
    )
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    args = parser.parse_args()
    if not 1 <= args.days <= MAX_DAYS:
        parser.error(f"--days must be between 1 and {MAX_DAYS}")

    wellness = get_recent_wellness_normalized(args.days)
    summary = migrate_legacy_context_notes(wellness)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
