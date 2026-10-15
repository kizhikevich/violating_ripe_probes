#!/usr/bin/env python3
"""Publish a semi-monthly list of root-DNS SOI-violating RIPE Atlas probe IDs.

Uses ONLY the daily JSON files already generated for the GitHub Pages checker.
A probe is included if flags['100_2'] is true on at least one available day
in the release period (100 km tolerance, >= 2 root letters on that day).

Release periods:
  YYYY-MM-15 -> YYYY-MM-01 through YYYY-MM-14
  YYYY-MM-01 -> previous month's 15th through previous month's last day

Output: a plain-text file containing one probe ID per line, without a header
or any additional columns.
"""

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


HERE = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = HERE / "site" / "data"
DEFAULT_OUTPUT_DIR = HERE.parent


def latest_release_date(today: date) -> date:
    return today.replace(day=15 if today.day >= 15 else 1)


def release_window(release: date) -> tuple[date, date]:
    if release.day not in (1, 15):
        raise ValueError("Release date must be the 1st or 15th of a month")

    end = release - timedelta(days=1)
    if release.day == 15:
        start = release.replace(day=1)
    else:
        start = end.replace(day=15)
    return start, end


def iter_dates(start: date, end: date):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def collect_violators(data_dir: Path, start: date, end: date):
    violating_ids = set()
    available_days = []
    missing_days = []

    for day in iter_dates(start, end):
        path = data_dir / f"{day.isoformat()}.json"
        if not path.is_file():
            missing_days.append(day.isoformat())
            continue

        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)

        if payload.get("date") != day.isoformat():
            raise ValueError(f"Unexpected date in {path}: {payload.get('date')!r}")

        probes = payload.get("probes")
        if not isinstance(probes, dict):
            raise ValueError(f"Missing probes dictionary in {path}")

        for probe_id, record in probes.items():
            flags = record.get("flags")
            if not isinstance(flags, dict) or "100_2" not in flags:
                raise ValueError(f"Missing 100_2 flag for probe {probe_id} in {path}")

            if flags["100_2"] is True:
                violating_ids.add(int(probe_id))

        available_days.append(day.isoformat())

    return sorted(violating_ids), available_days, missing_days


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--release-date",
        help="YYYY-MM-DD (must be the 1st or 15th); defaults to latest release date in UTC",
    )
    parser.add_argument(
        "--data-dir", type=Path, default=DEFAULT_DATA_DIR,
        help="Directory with daily site/data/YYYY-MM-DD.json files",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
        help="Repository root, where probe_ids_YYYYMMDD.txt is written",
    )
    parser.add_argument(
        "--require-all-days", action="store_true",
        help="Fail if any daily JSON file is missing in the release window",
    )
    args = parser.parse_args()

    today_utc = datetime.now(timezone.utc).date()
    release = (
        date.fromisoformat(args.release_date)
        if args.release_date
        else latest_release_date(today_utc)
    )
    if release > today_utc:
        parser.error("Release date cannot be in the future")

    try:
        start, end = release_window(release)
        ids, available, missing = collect_violators(args.data_dir, start, end)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    if not available:
        parser.error(f"No daily JSON files found for {start} through {end}")
    if missing:
        print(
            f"WARNING: {len(missing)} day(s) have no published data: "
            + ", ".join(missing),
            file=sys.stderr,
        )
        if args.require_all_days:
            parser.error("Incomplete release window; refusing to publish")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_name = f"probe_ids_{release:%Y%m%d}.txt"
    output_path = args.output_dir / output_name

    # One ID per line, numeric order; no header or additional columns.
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        for probe_id in ids:
            handle.write(f"{probe_id}\n")

    print(f"Release: {release.isoformat()}")
    print(f"Window: {start.isoformat()} through {end.isoformat()} (inclusive)")
    print(f"Days available: {len(available)}; days missing: {len(missing)}")
    print(f"Distinct root-DNS violators (100 km, >=2 letters/day): {len(ids):,}")
    print(f"Wrote {output_path}")

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as handle:
            handle.write(f"filename={output_name}\n")
            handle.write(f"probe_count={len(ids)}\n")
            handle.write(f"available_days={len(available)}\n")
            handle.write(f"missing_days={len(missing)}\n")


if __name__ == "__main__":
    main()
