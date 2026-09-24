#!/usr/bin/env python3

import argparse
import bz2
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


API_URL = "https://atlas.ripe.net/api/v2/measurements"
META_LATEST_URL = "https://ftp.ripe.net/ripe/atlas/probes/archive/meta-latest"

SOI_SPEED_KM_S = 199_862.638
EARTH_RADIUS_KM = 6371.0
SCHEDULED_SLOTS = ("06", "12", "18")


# hostname.bind measurement IDs, IPv4 only.
measurements = {
    "a": 10309,
    "b": 10310,
    "c": 10311,
    "d": 10312,
    "e": 10313,
    "f": 10304,
    "g": 10314,
    "h": 10315,
    "i": 10305,
    "j": 10316,
    "k": 10301,
    "l": 10308,
    "m": 10306,
}


regexes = {
    "a": [
        re.compile(r"^(?:rootns-|nnn1-)([a-z]{3})\d+$"),
        re.compile(r"^(?:rootns-|nnn1-)el([a-z]{3})\d+$"),
        re.compile(r"^(?:rootns-|nnn1-)(?:[a-z]{2})([a-z]{3})-\d+[a-z]?$"),
    ],
    "b": re.compile(r"^b\d-([a-z]{3})$"),
    "c": re.compile(r"^([a-z]{3})\d[a-z]\.c\.root-servers\.org$"),
    "d": re.compile(r"^([a-z]{4})\d\.droot\.maxgigapop\.net$"),
    "e": re.compile(r"^(?:[a-z]\d+)\.([a-z]{3})[a-z0-9]?\.eroot$"),
    "f": re.compile(r"^([a-z]{3})(?:\d[a-z]|\.cf)\.f\.root-servers\.org$"),
    "g": re.compile(r"^groot-?-(.*?)-.*?(\.net)?$"),
    "h": re.compile(r"^\d+\.([a-z]{3})\.h\.root-servers\.org$"),
    "i": re.compile(r"^s\d\.([a-z]{3})$"),
    "j": [
        re.compile(r"^(?:rootns-|nnn1-)([a-z]{3})\d+$"),
        re.compile(r"^(?:rootns-|nnn1-)el([a-z]{3})\d+$"),
        re.compile(r"^(?:rootns-|nnn1-)(?:[a-z]{2})([a-z]{3})-\d+[a-z]?$"),
    ],
    "k": re.compile(r"^.*?\.([a-z]{2}-[a-z]{3})\.k\.ripe\.net$"),
    "l": re.compile(r"^([a-z]{2}-[a-z]{3})-[a-z]{2}$"),
    "m": re.compile(r"^m-([a-z]{3})(-[a-z]+)?-\d$"),
}


def make_session():
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        respect_retry_after_header=True,
    )

    adapter = HTTPAdapter(
        max_retries=retry,
        pool_connections=20,
        pool_maxsize=20,
    )

    session = requests.Session()
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": "ripe-atlas-soi-checker/1.0"})
    return session


def normalize_hostname(hostname):
    if hostname is None:
        return None

    hostname = str(hostname).strip().strip("\"'").rstrip(".").lower()
    return hostname or None


def parse_site(letter, hostname):
    hostname = normalize_hostname(hostname)
    if hostname is None:
        return None

    re_def = regexes.get(letter)

    if isinstance(re_def, list):
        for regex in re_def:
            match = regex.match(hostname)
            if match:
                return match.group(1)
    else:
        match = re_def.match(hostname)
        if match:
            return match.group(1)

    return None


def extract_hostname_from_full_result(result):
    """Fallback parser for a normal RIPE Atlas DNS result object."""
    answers = result.get("answers") or []

    for answer in answers:
        rdata = answer.get("RDATA")

        if isinstance(rdata, list):
            candidates = rdata
        elif isinstance(rdata, str):
            candidates = [rdata]
        else:
            continue

        for value in candidates:
            hostname = normalize_hostname(value)
            if hostname:
                return hostname

    return None


def fetch_latest_measurements(session, letter, proto=4, freshness=900):
    """
    Fetch one compact latest-result snapshot for one root letter.

    This deliberately uses the same compact /latest/ request shape as the
    original checker code: only RTT and hostname.bind response are requested.
    """
    measurement_id = measurements[letter]
    if proto == 6:
        measurement_id += 1000

    url = f"{API_URL}/{measurement_id}/latest/"
    params = {
        "fields": "responses.0.response_time,responses.0.abuf.answers.0.data.0",
        "freshness": freshness,
        "use_keys": "true",
    }

    print(f"Fetching latest {letter.upper()} root ({measurement_id})...")

    response = session.get(url, params=params, timeout=(20, 120))
    response.raise_for_status()
    results = response.json()

    rows = []
    stats = Counter()

    # Preferred compact shape when use_keys=true:
    #   {"1234": [[12.3, "hostname"]], ...}
    if isinstance(results, dict):
        for probe_id, response_list in results.items():
            stats["returned"] += 1

            if not response_list:
                stats["empty"] += 1
                continue

            first = response_list[0]

            if not isinstance(first, (list, tuple)) or len(first) < 2:
                stats["unexpected_shape"] += 1
                continue

            rtt, hostname = first[0], first[1]
            row = _build_latest_row(
                probe_id=probe_id,
                letter=letter,
                measurement_id=measurement_id,
                rtt=rtt,
                hostname=hostname,
                stats=stats,
            )
            if row is not None:
                rows.append(row)

    # Fallback if RIPE returns ordinary result objects instead.
    elif isinstance(results, list):
        for obj in results:
            stats["returned"] += 1

            if not isinstance(obj, dict):
                stats["unexpected_shape"] += 1
                continue

            probe_id = obj.get("prb_id")
            dns_result = obj.get("result")

            if probe_id is None or not isinstance(dns_result, dict):
                stats["unexpected_shape"] += 1
                continue

            rtt = dns_result.get("rt")
            hostname = extract_hostname_from_full_result(dns_result)

            row = _build_latest_row(
                probe_id=probe_id,
                letter=letter,
                measurement_id=measurement_id,
                rtt=rtt,
                hostname=hostname,
                stats=stats,
            )
            if row is not None:
                rows.append(row)

    else:
        raise RuntimeError(
            f"Unexpected /latest/ response type for {letter.upper()}: "
            f"{type(results).__name__}"
        )

    print(
        f"  {letter.upper()}: {stats['returned']:,} returned, "
        f"{stats['usable']:,} usable, "
        f"{stats['unparsed_hostname']:,} unparsed hostnames"
    )

    return rows, stats


def _build_latest_row(probe_id, letter, measurement_id, rtt, hostname, stats):
    if rtt is None:
        stats["missing_rtt"] += 1
        return None

    try:
        rtt = float(rtt)
    except (TypeError, ValueError):
        stats["invalid_rtt"] += 1
        return None

    if not np.isfinite(rtt) or rtt < 0:
        stats["invalid_rtt"] += 1
        return None

    hostname = normalize_hostname(hostname)
    if hostname is None:
        stats["missing_hostname"] += 1
        return None

    site = parse_site(letter, hostname)
    if site is None:
        stats["unparsed_hostname"] += 1
        return None

    stats["usable"] += 1

    return {
        "probe_id": int(probe_id),
        "root_letter": letter,
        "hostname": hostname,
        "site": site,
        "rtt_ms": rtt,
        "measurement_id": measurement_id,
    }


def fetch_probe_metadata(session):
    print("Downloading latest probe metadata...")
    response = session.get(META_LATEST_URL, timeout=(20, 120))
    response.raise_for_status()

    decompressed = bz2.decompress(response.content)
    meta_json = json.loads(decompressed)

    probes = []
    for probe in meta_json.get("objects", []):
        probes.append(
            {
                "probe_id": probe["id"],
                "asn_v4": probe.get("asn_v4"),
                "asn_v6": probe.get("asn_v6"),
                "country": probe.get("country_code"),
                "latitude": probe.get("latitude"),
                "longitude": probe.get("longitude"),
                "status": probe.get("status"),
            }
        )

    df = pd.DataFrame(probes)
    print(f"Loaded {len(df):,} probe metadata records")
    return df


def load_root_locations(path):
    """
    Load the root_locations.csv supplied with this project.

    Expected columns:
        ns, latitude, longitude

    The join is performed against the exact normalized hostname returned by
    hostname.bind. Duplicate ns rows are allowed only when their coordinates
    agree, in which case they are collapsed.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Root-location file not found: {path}. "
            "The violation calculation requires root coordinates."
        )

    locations = pd.read_csv(path)
    required = {"ns", "latitude", "longitude"}
    missing = required - set(locations.columns)

    if missing:
        raise ValueError(
            f"{path} is missing required columns: {sorted(missing)}. "
            "Expected ns, latitude, longitude."
        )

    locations = locations[["ns", "latitude", "longitude"]].copy()
    locations["hostname_key"] = locations["ns"].map(normalize_hostname)

    conflicting = []
    for hostname_key, group in locations.groupby("hostname_key", dropna=False):
        if group[["latitude", "longitude"]].drop_duplicates().shape[0] > 1:
            conflicting.append(hostname_key)

    if conflicting:
        raise ValueError(
            "root_locations.csv has duplicate hostnames with conflicting "
            f"coordinates. Examples: {conflicting[:10]}"
        )

    locations = locations.drop_duplicates("hostname_key", keep="first")
    locations = locations.rename(
        columns={
            "ns": "root_ns",
            "latitude": "dns_lat",
            "longitude": "dns_lon",
        }
    )

    print(f"Loaded {len(locations):,} unique root-instance hostnames from {path}")
    return locations


def furthest_possible_distance(rtt_ms):
    return SOI_SPEED_KM_S * (rtt_ms / 1000.0) / 2.0


def haversine_distance(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(
        np.radians,
        [lat1, lon1, lat2, lon2],
    )

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        np.sin(dlat / 2.0) ** 2
        + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2.0) ** 2
    )
    a = np.clip(a, 0, 1)

    return EARTH_RADIUS_KM * 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))


def add_soi_columns(df):
    df = df.copy()

    df["furthest_possible_km"] = furthest_possible_distance(df["rtt_ms"])
    df["haversine_distance_km"] = np.nan

    valid = (
        df["latitude"].notna()
        & df["longitude"].notna()
        & df["dns_lat"].notna()
        & df["dns_lon"].notna()
    )

    df.loc[valid, "haversine_distance_km"] = haversine_distance(
        df.loc[valid, "latitude"],
        df.loc[valid, "longitude"],
        df.loc[valid, "dns_lat"],
        df.loc[valid, "dns_lon"],
    )

    df["excess_km"] = df["haversine_distance_km"] - df["furthest_possible_km"]
    df["location_available"] = valid

    # Exact two UI thresholds.
    df["violates_0km"] = valid & (df["excess_km"] > 0)
    df["violates_100km"] = valid & (df["excess_km"] > 100)

    return df


def resolve_slot(now_utc, requested_slot):
    if requested_slot is not None:
        return requested_slot

    # Scheduled GitHub jobs can start a little late. Treat any run after a
    # scheduled time as belonging to the most recent slot that day.
    if now_utc.hour >= 18:
        return "18"
    if now_utc.hour >= 12:
        return "12"
    if now_utc.hour >= 6:
        return "06"

    raise ValueError(
        f"Current UTC hour is {now_utc.hour:02d}, before the first scheduled "
        "slot. For a manual test, pass --slot 06, --slot 12, or --slot 18."
    )


def save_snapshot(df, output_dir, target_date, slot):
    snapshots_dir = output_dir / "snapshots"
    snapshots_dir.mkdir(parents=True, exist_ok=True)

    path = snapshots_dir / f"{target_date}_{slot}.parquet"
    df.to_parquet(path, index=False, compression="zstd")
    print(f"Wrote snapshot: {path}")
    return path


def load_available_snapshots(output_dir, target_date):
    snapshots_dir = output_dir / "snapshots"
    frames = []
    loaded_slots = []

    for slot in SCHEDULED_SLOTS:
        path = snapshots_dir / f"{target_date}_{slot}.parquet"
        if path.exists():
            frame = pd.read_parquet(path)
            frames.append(frame)
            loaded_slots.append(slot)

    if not frames:
        raise RuntimeError(f"No snapshots found for {target_date}")

    return pd.concat(frames, ignore_index=True), loaded_slots


def choose_daily_minima(snapshot_df):
    """
    Keep the complete row corresponding to the minimum RTT observed across
    the available 06/12/18 snapshots for each probe x root letter.
    """
    counts = (
        snapshot_df.groupby(["probe_id", "root_letter"])
        .size()
        .rename("num_snapshots_seen")
        .reset_index()
    )

    idx = (
        snapshot_df.groupby(["probe_id", "root_letter"])["rtt_ms"]
        .idxmin()
    )

    minima = snapshot_df.loc[idx].copy().reset_index(drop=True)
    minima = minima.merge(
        counts,
        on=["probe_id", "root_letter"],
        how="left",
        validate="one_to_one",
    )

    minima = minima.rename(
        columns={
            "slot_utc": "min_rtt_slot_utc",
            "snapshot_fetched_at_utc": "min_rtt_snapshot_fetched_at_utc",
        }
    )

    return minima


def make_probe_summary(observations, target_date, loaded_slots):
    summary = (
        observations.groupby("probe_id")
        .agg(
            num_roots_observed=("root_letter", "nunique"),
            num_roots_with_location=("location_available", "sum"),
            num_violating_letters_0km=("violates_0km", "sum"),
            num_violating_letters_100km=("violates_100km", "sum"),
        )
        .reset_index()
    )

    summary["violates_0km_1letter"] = summary["num_violating_letters_0km"] >= 1
    summary["violates_0km_2letters"] = summary["num_violating_letters_0km"] >= 2
    summary["violates_100km_1letter"] = summary["num_violating_letters_100km"] >= 1
    summary["violates_100km_2letters"] = summary["num_violating_letters_100km"] >= 2

    summary.insert(0, "date", str(target_date))
    summary["snapshot_slots_loaded"] = ",".join(loaded_slots)
    summary["num_snapshot_slots_loaded"] = len(loaded_slots)

    # Add one copy of the probe metadata for convenient UI display.
    metadata_cols = [
        "probe_id",
        "latitude",
        "longitude",
        "country",
        "asn_v4",
        "asn_v6",
        "status",
    ]
    available_metadata_cols = [
        c for c in metadata_cols if c in observations.columns
    ]

    probe_meta = (
        observations[available_metadata_cols]
        .drop_duplicates("probe_id")
    )

    summary = summary.merge(
        probe_meta,
        on="probe_id",
        how="left",
        validate="one_to_one",
    )

    return summary


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Fetch one RIPE Atlas latest snapshot at 06/12/18 UTC, save it, "
            "then recompute the day's minimum RTT per probe/root across all "
            "scheduled snapshots currently available."
        )
    )

    parser.add_argument(
        "--slot",
        choices=SCHEDULED_SLOTS,
        help=(
            "UTC snapshot slot: 06, 12, or 18. If omitted, infer it from "
            "the current UTC hour. Use this option for manual/local tests."
        ),
    )

    parser.add_argument(
        "--date",
        help=(
            "Date label YYYY-MM-DD. Normally omit this and use today's UTC "
            "date. IMPORTANT: /latest/ always fetches current data; this "
            "option is only for controlled local testing."
        ),
    )

    parser.add_argument(
        "--output-dir",
        default="data",
        help="Output directory. Default: data/",
    )

    parser.add_argument(
        "--root-locations",
        default="root_locations.csv",
        help="Root instance CSV. Default: root_locations.csv",
    )

    parser.add_argument(
        "--freshness",
        type=int,
        default=900,
        help="Maximum result age in seconds for /latest/. Default: 900.",
    )

    parser.add_argument(
        "--csv",
        action="store_true",
        help="Also write CSV copies of observations and summary.",
    )

    return parser.parse_args()


def main():
    args = parse_args()
    now_utc = datetime.now(timezone.utc)
    slot = resolve_slot(now_utc, args.slot)

    if args.date:
        target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        if target_date != now_utc.date():
            print(
                "WARNING: --date only changes the file/date label. "
                "/latest/ still returns current measurements."
            )
    else:
        target_date = now_utc.date()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print()
    print("=" * 64)
    print("RIPE Atlas SOI scheduled snapshot")
    print("=" * 64)
    print(f"UTC date:       {target_date}")
    print(f"Scheduled slot: {slot}:00 UTC")
    print(f"Fetched at:     {now_utc.isoformat()}")
    print()

    session = make_session()

    # 1. Fetch a compact latest snapshot for all 13 root letters.
    all_rows = []
    stats_by_letter = {}

    for letter in measurements:
        rows, stats = fetch_latest_measurements(
            session=session,
            letter=letter,
            proto=4,
            freshness=args.freshness,
        )
        all_rows.extend(rows)
        stats_by_letter[letter] = stats

    snapshot = pd.DataFrame(all_rows)
    if snapshot.empty:
        raise RuntimeError("No usable RIPE Atlas latest results were returned.")

    if snapshot.duplicated(["probe_id", "root_letter"]).any():
        raise RuntimeError(
            "Unexpected duplicate probe/root rows within one latest snapshot."
        )

    snapshot["date"] = str(target_date)
    snapshot["slot_utc"] = slot
    snapshot["snapshot_fetched_at_utc"] = now_utc.isoformat()

    print()
    print(
        f"Current snapshot: {len(snapshot):,} probe/root rows for "
        f"{snapshot['probe_id'].nunique():,} probes"
    )

    save_snapshot(snapshot, output_dir, target_date, slot)

    # 2. Combine whichever of today's 06/12/18 snapshots exist and select
    #    the minimum RTT for each probe x root letter.
    daily_snapshots, loaded_slots = load_available_snapshots(
        output_dir,
        target_date,
    )

    minima = choose_daily_minima(daily_snapshots)

    print(
        f"Daily minimum now uses slots {loaded_slots}: "
        f"{len(minima):,} probe/root rows"
    )

    # 3. Enrich the selected minimum-RTT rows only. This is much smaller than
    #    enriching all raw/latest responses repeatedly.
    meta_df = fetch_probe_metadata(session)
    observations = minima.merge(
        meta_df,
        on="probe_id",
        how="left",
        validate="many_to_one",
    )

    observations["hostname_key"] = observations["hostname"].map(normalize_hostname)

    root_locations = load_root_locations(args.root_locations)
    observations = observations.merge(
        root_locations,
        on="hostname_key",
        how="left",
        validate="many_to_one",
    )

    missing_probe_coords = (
        observations["latitude"].isna() | observations["longitude"].isna()
    ).sum()
    missing_root_coords = (
        observations["dns_lat"].isna() | observations["dns_lon"].isna()
    ).sum()

    print(f"Rows missing probe coordinates: {missing_probe_coords:,}")
    print(f"Rows missing root coordinates:  {missing_root_coords:,}")

    # 4. Precompute both buffer choices and probe-level one/two-letter rules.
    observations = add_soi_columns(observations)
    summary = make_probe_summary(observations, target_date, loaded_slots)

    # 5. Save current daily aggregate. The 12:00 run overwrites the 06:00
    #    aggregate with min(06,12); the 18:00 run overwrites it with
    #    min(06,12,18). The individual snapshots remain saved separately.
    observations_dir = output_dir / "observations"
    summary_dir = output_dir / "summary"
    observations_dir.mkdir(parents=True, exist_ok=True)
    summary_dir.mkdir(parents=True, exist_ok=True)

    observations_path = observations_dir / f"{target_date}.parquet"
    summary_path = summary_dir / f"{target_date}.parquet"

    observations.to_parquet(
        observations_path,
        index=False,
        compression="zstd",
    )
    summary.to_parquet(
        summary_path,
        index=False,
        compression="zstd",
    )

    print()
    print(f"Wrote observations: {observations_path}")
    print(f"Wrote summary:      {summary_path}")

    if args.csv:
        observations.to_csv(
            observations_dir / f"{target_date}.csv",
            index=False,
        )
        summary.to_csv(
            summary_dir / f"{target_date}.csv",
            index=False,
        )

    print()
    print("Violation summary:")
    print(
        f"  0 km, >=1 letter:   "
        f"{summary['violates_0km_1letter'].sum():,} probes"
    )
    print(
        f"  0 km, >=2 letters:  "
        f"{summary['violates_0km_2letters'].sum():,} probes"
    )
    print(
        f"  100 km, >=1 letter: "
        f"{summary['violates_100km_1letter'].sum():,} probes"
    )
    print(
        f"  100 km, >=2 letters:"
        f" {summary['violates_100km_2letters'].sum():,} probes"
    )

    print()
    print("Hostname parsing summary:")
    for letter in measurements:
        stats = stats_by_letter[letter]
        print(
            f"  {letter.upper()}: {stats['usable']:,} usable; "
            f"{stats['unparsed_hostname']:,} unparsed"
        )

    print()
    print("Done.")


if __name__ == "__main__":
    main()