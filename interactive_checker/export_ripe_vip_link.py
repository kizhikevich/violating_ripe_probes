#!/usr/bin/env python3
"""Generate an evidence-based, twice-monthly RIPE Atlas probe-location JSONL feed.

The feed uses data the SOI Checker already publishes at
interactive_checker/site/data/YYYY-MM-DD.json. A probe qualifies only after
**two different UTC dates** in the release period independently meet the
existing 100km-buffer, >=2-distinct-letters-on-a-day rule.

The measurements demonstrate inconsistency with the *reported* probe
location; they do NOT establish the probe's real coordinates. The JSONL
lat/lon fields intentionally contain those disputed RIPE Atlas *reported*
coordinates, taken from the last qualifying measurement day in the period.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parent
DEFAULT_DATA_DIR = HERE / "site" / "data"
DEFAULT_OUTPUT_DIR = REPOSITORY / "ripe_vip_link"
DEFAULT_SITE_FILE = HERE / "site" / "ripe_vip_link.jsonl"

SOURCE = "UCSD RIPE Atlas Geolocation Study"
SOURCE_EMAIL = "ucsd_ripe_geolocation@googlegroups.com"
MINIMUM_DISTINCT_DAYS = 2
BUFFER_KM = 100.0

# Only unambiguous, well-known site abbreviations are expanded. Unknown codes
# are left verbatim, not reverse-geocoded or invented.
SITE_CITY = {
    "ams": "Amsterdam", "bru": "Brussels", "cdg": "Paris", "dfw": "Dallas",
    "fra": "Frankfurt", "ham": "Hamburg", "hkg": "Hong Kong", "iad": "Ashburn",
    "lax": "Los Angeles", "lhr": "London", "lon": "London", "mad": "Madrid",
    "mia": "Miami", "mil": "Milan", "nrt": "Tokyo", "nyc": "New York",
    "ord": "Chicago", "par": "Paris", "rom": "Rome", "sfo": "San Francisco",
    "sin": "Singapore", "sjc": "San Jose", "syd": "Sydney", "vie": "Vienna",
}

# Adapted from the same parse_site patterns in interactive_checker/fetch_data.py,
# without importing the measurement-fetcher's third-party dependencies.
SITE_REGEXES = {
    "a": [r"^(?:rootns-|nnn1-)([a-z]{3})\d+$", r"^(?:rootns-|nnn1-)el([a-z]{3})\d+$", r"^(?:rootns-|nnn1-)(?:[a-z]{2})([a-z]{3})-\d+[a-z]?$"],
    "b": [r"^b\d-([a-z]{3})$"],
    "c": [r"^([a-z]{3})\d[a-z]\.c\.root-servers\.org$"],
    "d": [r"^([a-z]{4})\d\.droot\.maxgigapop\.net$"],
    "e": [r"^(?:[a-z]\d+)\.([a-z]{3})[a-z0-9]?\.eroot$"],
    "f": [r"^([a-z]{3})(?:\d[a-z]|\.cf)\.f\.root-servers\.org$"],
    "g": [r"^groot-?-(.*?)-.*?(?:\.net)?$"],
    "h": [r"^\d+\.([a-z]{3})\.h\.root-servers\.org$"],
    "i": [r"^s\d\.([a-z]{3})$"],
    "j": [r"^(?:rootns-|nnn1-)([a-z]{3})\d+$", r"^(?:rootns-|nnn1-)el([a-z]{3})\d+$", r"^(?:rootns-|nnn1-)(?:[a-z]{2})([a-z]{3})-\d+[a-z]?$"],
    "k": [r"^.*?\.([a-z]{2}-[a-z]{3})\.k\.ripe\.net$"],
    "l": [r"^([a-z]{2}-[a-z]{3})-[a-z]{2}$"],
    "m": [r"^m-([a-z]{3})(?:-[a-z]+)?-\d$"],
}


def latest_release_date(today: date) -> date:
    return today.replace(day=15 if today.day >= 15 else 1)


def release_window(release: date) -> tuple[date, date]:
    if release.day not in (1, 15):
        raise ValueError("Release date must be the 1st or 15th of a month")
    end = release - timedelta(days=1)
    start = release.replace(day=1) if release.day == 15 else end.replace(day=15)
    return start, end


def iter_dates(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def finite_number(value):
    """Float from JSON, excluding bool/NaN/inf and malformed strings."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def site_label(violation: dict) -> str:
    root = str(violation.get("root") or "").lower()
    hostname = str(violation.get("hostname") or "").strip().strip("\"'").rstrip(".").lower()
    code = None
    for regex in SITE_REGEXES.get(root, []):
        match = re.match(regex, hostname)
        if match:
            code = match.group(1)
            break
    if code:
        code = code.lower()
        city_code = code.rsplit("-", 1)[-1]
        return SITE_CITY.get(city_code, code)

    # We have the measured server's coordinates, but do not confuse them with
    # an inferred/corrected probe location or with a DNS-root identifier.
    latitude = finite_number(violation.get("dns_lat"))
    longitude = finite_number(violation.get("dns_lon"))
    if latitude is not None and longitude is not None:
        return f"near ({latitude:.3f}, {longitude:.3f})"
    return "an unspecified location"


def evidence_for_day(record: dict) -> dict | None:
    """Pick the clearest individual observation from a qualifying day.

    A day counts only with 2 distinct letters showing excess > 100 km.
    Validate detailed evidence in addition to the summary flag to avoid
    silently publishing a malformed/inconsistent day's data.
    """
    flags = record.get("flags")
    if not isinstance(flags, dict) or flags.get("100_2") is not True:
        return None

    violations = record.get("violations")
    if not isinstance(violations, list):
        return None
    by_letter = {}
    for violation in violations:
        if not isinstance(violation, dict):
            continue
        root = str(violation.get("root") or "").upper().strip()
        excess = finite_number(violation.get("excess_km"))
        rtt = finite_number(violation.get("rtt_ms"))
        distance = finite_number(violation.get("distance_km"))
        if not root or excess is None or rtt is None or distance is None:
            continue
        if excess <= BUFFER_KM or rtt < 0 or distance < 0:
            continue
        previous = by_letter.get(root)
        if previous is None or excess > finite_number(previous["excess_km"]):
            by_letter[root] = violation
    if len(by_letter) < 2:
        return None
    return max(by_letter.values(), key=lambda value: finite_number(value["excess_km"]))


def reported_coordinates(probe_id: int, day: date, record: dict) -> tuple[float, float]:
    """Return RIPE Atlas's *reported* location, not an inferred true location.

    These fields come directly from the daily site's probe record. Never use
    the measured server's dns_lat/dns_lon or a geolocation guess instead.
    """
    lat = finite_number(record.get("latitude"))
    lon = finite_number(record.get("longitude"))
    if lat is None or lon is None or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError(
            f"Missing or invalid RIPE Atlas reported coordinates for probe "
            f"{probe_id} on {day}; expected numeric latitude and longitude"
        )
    return lat, lon


def description_for(evidence: dict) -> str:
    rtt = float(evidence["rtt_ms"])
    distance = float(evidence["distance_km"])
    site = site_label(evidence)
    return (
        f"Probe reports a {rtt:.3f}ms RTT to a server at site {site}. "
        f"With a 100km error radius, a {rtt:.3f}ms RTT is unlikely "
        f"to correspond to a ~{distance:.2f}km physical separation."
    )


def build_feed(data_dir: Path, start: date, end: date):
    # Each probe counts once per *UTC day*. The 2-letter condition is already
    # required independently on each qualifying day.
    events = {}
    available, missing, inconsistent = [], [], []
    for day in iter_dates(start, end):
        path = data_dir / f"{day.isoformat()}.json"
        if not path.is_file():
            missing.append(day.isoformat())
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("date") != day.isoformat():
            raise ValueError(f"Date mismatch in {path}")
        probes = payload.get("probes")
        if not isinstance(probes, dict):
            raise ValueError(f"Missing probes dictionary in {path}")
        seen_ids = set()
        for key, record in probes.items():
            probe_id = int(key)
            if probe_id in seen_ids:
                raise ValueError(f"Duplicate numerical probe ID {probe_id} in {path}")
            seen_ids.add(probe_id)
            if not isinstance(record, dict):
                raise ValueError(f"Malformed probe record {key} in {path}")
            flags = record.get("flags")
            if not isinstance(flags, dict) or "100_2" not in flags:
                raise ValueError(f"Missing 100_2 flag for probe {key} in {path}")
            if flags["100_2"] is not True:
                continue
            evidence = evidence_for_day(record)
            if evidence is None:
                inconsistent.append(f"{key} ({day.isoformat()})")
                continue
            events.setdefault(probe_id, []).append((day, evidence, record))
        available.append(day.isoformat())

    if not available:
        raise ValueError(f"No daily site data found for {start} through {end}")
    if inconsistent:
        raise ValueError(
            f"{len(inconsistent)} flagged records have insufficient or inconsistent "
            f"detailed evidence (examples: {', '.join(inconsistent[:5])})"
        )

    rows = []
    for probe_id, observations in sorted(events.items()):
        if len(observations) < MINIMUM_DISTINCT_DAYS:
            continue
        # The first qualifying day determines date_detected. Use the reported
        # coordinates and the measured RTT from the LAST qualifying day so the
        # disputed coordinates and supporting evidence refer to the same day.
        first_day = observations[0][0]  # days read in ascending order
        latest_day, latest_evidence, latest_record = observations[-1]
        latitude, longitude = reported_coordinates(probe_id, latest_day, latest_record)
        rows.append({
            "probe": probe_id,
            "source": SOURCE,
            "source_email": SOURCE_EMAIL,
            "description": description_for(latest_evidence),
            # First confirmed qualifying UTC day, at UTC midnight. The website
            # summaries don't retain the exact timestamp of each ping.
            "date_detected": int(datetime.combine(first_day, datetime.min.time(), timezone.utc).timestamp()),
            "lat": latitude,
            "lon": longitude,
        })
    return rows, available, missing


def write_feeds(rows: list[dict], release: date, output_dir: Path, site_file: Path):
    contents = "".join(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n" for row in rows)
    output_dir.mkdir(parents=True, exist_ok=True)
    site_file.parent.mkdir(parents=True, exist_ok=True)
    archived = output_dir / f"ripe_vip_link_{release:%Y%m%d}.jsonl"
    latest = output_dir / "latest.jsonl"
    for path in (archived, latest, site_file):
        path.write_text(contents, encoding="utf-8", newline="")
    return archived, latest, site_file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-date", help="YYYY-MM-DD (1st or 15th); blank = latest in UTC")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--site-file", type=Path, default=DEFAULT_SITE_FILE)
    parser.add_argument("--require-all-days", action="store_true",
                        help="Refuse release if any daily JSON file is missing")
    args = parser.parse_args()
    utc_today = datetime.now(timezone.utc).date()
    try:
        release = date.fromisoformat(args.release_date) if args.release_date else latest_release_date(utc_today)
        if release > utc_today:
            parser.error("Release cannot be in the future")
        start, end = release_window(release)
        rows, available, missing = build_feed(args.data_dir, start, end)
        if missing:
            print(f"WARNING: Missing {len(missing)} days: {', '.join(missing)}", file=sys.stderr)
            if args.require_all_days:
                parser.error("Release window is incomplete; refusing publication")
        if len(available) < MINIMUM_DISTINCT_DAYS:
            parser.error(f"Need at least {MINIMUM_DISTINCT_DAYS} daily files for independent confirmations")
        archived, latest, site = write_feeds(rows, release, args.output_dir, args.site_file)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    print(f"Release {release}: data {start} to {end}")
    print(f"Days available: {len(available)}; missing: {len(missing)}")
    print(f"Probes with >=2 independent qualifying UTC days: {len(rows)}")
    print(f"Wrote {archived}; {latest}; {site}")
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as handle:
            handle.write(f"snapshot=ripe_vip_link/{archived.name}\n")
            handle.write(f"count={len(rows)}\n")
            handle.write(f"available_days={len(available)}\n")
            handle.write(f"missing_days={len(missing)}\n")


if __name__ == "__main__":
    main()
