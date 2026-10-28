#!/usr/bin/env python3

import argparse
import bz2
import json
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


API_URL = "https://atlas.ripe.net/api/v2/measurements"
META_LATEST_URL = "https://ftp.ripe.net/ripe/atlas/probes/archive/meta-latest"
META_ARCHIVE_URL = (
    "https://ftp.ripe.net/ripe/atlas/probes/archive/"
    "{year}/{month:02d}/{datestr}.json.bz2"
)

SOI_SPEED_KM_S = 199_862.638
EARTH_RADIUS_KM = 6371.0
SCHEDULED_SLOTS = ("06", "12", "18")
WINDOW_MINUTES = 5
# Exclude this mislocated instance even if it remains in root_locations.csv.
EXCLUDED_HOSTNAMES = frozenset({"s1.ccp"})


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


def slot_window(target_day, slot, window_minutes=WINDOW_MINUTES):
    """Return the UTC target instant and its accepted [start, stop) window."""
    target = datetime.combine(target_day, datetime.min.time(), timezone.utc)
    target += timedelta(hours=int(slot))
    delta = timedelta(minutes=window_minutes)
    return target, target - delta, target + delta


def fetch_historical_slot(session, letter, target_day, slot, window_minutes=5):
    """Fetch historical hostname.bind DNS results near a fixed UTC instant.

    One result per probe/root/slot is selected: the valid observation with
    the timestamp CLOSEST to the scheduled instant, not the lowest RTT.
    Ties use the earlier timestamp, then the lower RTT. Missing windows
    remain missing; there is no fallback to /latest/ or to another time.
    """
    measurement_id = measurements[letter]
    target, start, stop = slot_window(target_day, slot, window_minutes)
    params = {
        "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stop": stop.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    url = f"{API_URL}/{measurement_id}/results/"
    print(f"Fetching {letter.upper()} root ({measurement_id}), "
          f"{target_day} {slot}:00 UTC +/-{window_minutes}m...", flush=True)

    response = session.get(url, params=params, timeout=(20, 180))
    try:
        response.raise_for_status()
        results = response.json()
    finally:
        response.close()

    if not isinstance(results, list):
        raise RuntimeError(f"Unexpected historical results format for {letter}: "
                           f"{type(results).__name__}")

    stats = Counter()
    selected = {}
    for obj in results:
        stats["returned"] += 1
        if not isinstance(obj, dict):
            stats["invalid_result"] += 1
            continue

        try:
            probe_id = int(obj["prb_id"])
            timestamp = int(obj["timestamp"])
        except (KeyError, TypeError, ValueError, OverflowError):
            stats["missing_probe_or_timestamp"] += 1
            continue

        # Defend against unexpected API boundary behavior. RIPE timestamps
        # are Unix seconds (UTC); do not use the fetch time or local TZ.
        if not (start.timestamp() <= timestamp < stop.timestamp()):
            stats["outside_window"] += 1
            continue

        dns_result = obj.get("result")
        if not isinstance(dns_result, dict):
            stats["missing_dns_result"] += 1
            continue
        try:
            rtt = float(dns_result["rt"])
        except (KeyError, TypeError, ValueError, OverflowError):
            stats["invalid_rtt"] += 1
            continue
        if not np.isfinite(rtt) or rtt < 0:
            stats["invalid_rtt"] += 1
            continue

        hostname = extract_hostname_from_full_result(dns_result)
        if hostname is None:
            stats["missing_hostname"] += 1
            continue
        if hostname in EXCLUDED_HOSTNAMES:
            stats["excluded_hostname"] += 1
            continue
        site = parse_site(letter, hostname)
        if site is None:
            stats["unparsed_hostname"] += 1
            continue

        stats["usable"] += 1
        row = {
            "probe_id": probe_id,
            "root_letter": letter,
            "hostname": hostname,
            "site": site,
            "rtt_ms": rtt,
            "measurement_id": measurement_id,
            "timestamp": timestamp,
            "timestamp_utc": datetime.fromtimestamp(
                timestamp, timezone.utc
            ).isoformat(),
            "date": target_day.isoformat(),
            "slot_utc": slot,
        }
        # The scheduled time, not the lowest RTT in the window, decides
        # which result represents this slot.
        rank = (abs(timestamp - int(target.timestamp())), timestamp, rtt)
        previous = selected.get(probe_id)
        if previous is None or rank < previous[0]:
            selected[probe_id] = (rank, row)

    stats["selected_probes"] = len(selected)
    print(f"  {stats['returned']:,} returned; {stats['usable']:,} usable; "
          f"{stats['selected_probes']:,} selected; "
          f"{stats['excluded_hostname']:,} excluded", flush=True)
    return [item[1] for item in selected.values()], stats


def fetch_probe_metadata(session, target_day):
    """Use the target day's archived probe locations when available.

    If RIPE does not have that archive, fall back to the latest metadata.
    """
    datestr = target_day.strftime("%Y%m%d")
    archive_url = META_ARCHIVE_URL.format(
        year=target_day.year, month=target_day.month, datestr=datestr
    )
    meta_json = None
    for url in (archive_url, META_LATEST_URL):
        print(f"Fetching probe metadata: {url}", flush=True)
        response = session.get(url, timeout=(20, 120))
        try:
            if response.status_code == 404:
                print("  Archive unavailable; trying fallback.")
                continue
            response.raise_for_status()
            try:
                meta_json = json.loads(bz2.decompress(response.content))
            except (OSError, json.JSONDecodeError) as exc:
                print(f"  Metadata decode failed: {exc}")
                continue
            break
        finally:
            response.close()

    if meta_json is None:
        raise RuntimeError("Could not load probe metadata from archive or latest")

    probes = []
    for probe in meta_json.get("objects", []):
        probes.append({
            "probe_id": probe["id"],
            "asn_v4": probe.get("asn_v4"),
            "asn_v6": probe.get("asn_v6"),
            "country": probe.get("country_code"),
            "latitude": probe.get("latitude"),
            "longitude": probe.get("longitude"),
            "status": probe.get("status"),
        })
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


def save_snapshot(df, output_dir, target_date, slot):
    snapshots_dir = output_dir / "snapshots"
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    path = snapshots_dir / f"{target_date}_{slot}.parquet"
    df.to_parquet(path, index=False, compression="zstd")
    print(f"Wrote snapshot: {path}")
    return path


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



def _json_value(value):
    """Convert pandas/numpy scalars to JSON-safe Python values."""
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value)
    return str(value)


def write_web_data(summary, observations, target_date, loaded_slots, site_dir):
    """
    Write a compact static JSON file consumed by the GitHub Pages UI.

    The browser does not recompute SOI. Probe-level rule outcomes are exported
    explicitly, and detailed rows are exported only for observations that
    violate the 0 km threshold (the 100 km set is a subset of these rows).
    """
    site_dir = Path(site_dir)
    web_data_dir = site_dir / "data"
    web_data_dir.mkdir(parents=True, exist_ok=True)

    violating = observations.loc[observations["violates_0km"].fillna(False)].copy()
    details_by_probe = {}

    for row in violating.itertuples(index=False):
        detail = {
            "root": str(row.root_letter).upper(),
            "root_ns": _json_value(getattr(row, "root_ns", None)),
            "hostname": _json_value(getattr(row, "hostname", None)),
            "slot": _json_value(getattr(row, "min_rtt_slot_utc", None)),
            "measurement_timestamp_utc": _json_value(getattr(row, "timestamp_utc", None)),
            "rtt_ms": _json_value(getattr(row, "rtt_ms", None)),
            "distance_km": _json_value(getattr(row, "haversine_distance_km", None)),
            "max_feasible_km": _json_value(getattr(row, "furthest_possible_km", None)),
            "excess_km": _json_value(getattr(row, "excess_km", None)),
            "dns_lat": _json_value(getattr(row, "dns_lat", None)),
            "dns_lon": _json_value(getattr(row, "dns_lon", None)),
        }
        details_by_probe.setdefault(str(int(row.probe_id)), []).append(detail)

    probes = {}
    for row in summary.itertuples(index=False):
        probe_id = str(int(row.probe_id))
        probes[probe_id] = {
            "country": _json_value(getattr(row, "country", None)),
            "asn_v4": _json_value(getattr(row, "asn_v4", None)),
            "latitude": _json_value(getattr(row, "latitude", None)),
            "longitude": _json_value(getattr(row, "longitude", None)),
            "roots_observed": _json_value(getattr(row, "num_roots_observed", None)),
            "counts": {
                "0km": int(getattr(row, "num_violating_letters_0km")),
                "100km": int(getattr(row, "num_violating_letters_100km")),
            },
            "flags": {
                "0_1": bool(getattr(row, "violates_0km_1letter")),
                "0_2": bool(getattr(row, "violates_0km_2letters")),
                "100_1": bool(getattr(row, "violates_100km_1letter")),
                "100_2": bool(getattr(row, "violates_100km_2letters")),
            },
            "violations": details_by_probe.get(probe_id, []),
        }

    payload = {
        "date": str(target_date),
        "slots": list(loaded_slots),
        "probes": probes,
    }

    day_path = web_data_dir / f"{target_date}.json"
    day_path.write_text(
        json.dumps(payload, separators=(",", ":"), allow_nan=False),
        encoding="utf-8",
    )

    # Rebuild a tiny index of all dates currently available to the static site.
    dates = sorted(
        [path.stem for path in web_data_dir.glob("????-??-??.json")],
        reverse=True,
    )
    index_payload = {
        "latest": dates[0] if dates else None,
        "dates": dates,
    }
    (web_data_dir / "index.json").write_text(
        json.dumps(index_payload, separators=(",", ":")),
        encoding="utf-8",
    )

    print(f"Wrote web data:      {day_path}")
    print(f"Updated web index:   {web_data_dir / 'index.json'}")


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Once per day, retrieve yesterday's RIPE Atlas hostname.bind "
            "results closest to 06:00, 12:00 and 18:00 UTC."
        )
    )
    parser.add_argument(
        "--date",
        help="Historical UTC date YYYY-MM-DD (default: yesterday UTC).",
    )
    parser.add_argument(
        "--window-minutes",
        type=int,
        default=WINDOW_MINUTES,
        help="Accepted window on either side of each target (default: 5).",
    )
    parser.add_argument(
        "--output-dir", default="data", help="Output directory (default: data/)."
    )
    parser.add_argument(
        "--root-locations", default="root_locations.csv",
        help="Root instance CSV (default: root_locations.csv).",
    )
    parser.add_argument(
        "--site-dir", default="site",
        help="Static GitHub Pages directory (default: site/).",
    )
    parser.add_argument(
        "--csv", action="store_true", help="Also write CSV files."
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if not 1 <= args.window_minutes <= 30:
        raise ValueError("--window-minutes must be between 1 and 30")

    now_utc = datetime.now(timezone.utc)
    target_day = (
        date.fromisoformat(args.date)
        if args.date else now_utc.date() - timedelta(days=1)
    )
    if target_day >= now_utc.date():
        raise ValueError("Target date must be a completed UTC day (before today)")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    print("=" * 64)
    print("RIPE Atlas SOI historical daily collection")
    print(f"Target date: {target_day} (UTC)")
    print(f"Slots: {', '.join(SCHEDULED_SLOTS)} UTC")
    print(f"Window: +/-{args.window_minutes} minutes")
    print(f"Collector ran: {now_utc.isoformat()}")
    print("=" * 64, flush=True)

    session = make_session()
    frames = []
    stats_by_letter = {}
    for slot in SCHEDULED_SLOTS:
        rows_for_slot = []
        for letter in measurements:
            rows, stats = fetch_historical_slot(
                session, letter, target_day, slot, args.window_minutes
            )
            rows_for_slot.extend(rows)
            stats_by_letter[(slot, letter)] = stats
        if rows_for_slot:
            snapshot = pd.DataFrame(rows_for_slot)
            if snapshot.duplicated(["probe_id", "root_letter"]).any():
                raise RuntimeError(f"Duplicate probe/root rows in {slot} slot")
            snapshot["snapshot_fetched_at_utc"] = now_utc.isoformat()
            frames.append(snapshot)
            save_snapshot(snapshot, output_dir, target_day, slot)
        else:
            # Remove a previous run's stale snapshot for this slot so that
            # backfills never leave misleading artifacts behind.
            old_path = output_dir / "snapshots" / f"{target_day}_{slot}.parquet"
            if old_path.exists():
                old_path.unlink()
            print(f"WARNING: no observations for {slot}:00 UTC; "
                  "slot will be absent from daily data")

    if not frames:
        raise RuntimeError("No valid observations in any of the three windows")

    # Do not read old snapshot files. A rerun must replace previously
    # mis-timed data rather than accidentally merging it back in.
    all_snapshots = pd.concat(frames, ignore_index=True)
    loaded_slots = sorted(all_snapshots["slot_utc"].unique().tolist())
    minima = choose_daily_minima(all_snapshots)
    print(f"Daily minima: {len(minima):,} probe/root pairs "
          f"across slots {loaded_slots}")

    meta_df = fetch_probe_metadata(session, target_day)
    observations = minima.merge(
        meta_df, on="probe_id", how="left", validate="many_to_one"
    )
    observations["hostname_key"] = observations["hostname"].map(normalize_hostname)
    root_locations = load_root_locations(args.root_locations)
    observations = observations.merge(
        root_locations, on="hostname_key", how="left", validate="many_to_one"
    )
    print("Missing probe coordinates:",
          int((observations["latitude"].isna() |
               observations["longitude"].isna()).sum()))
    print("Missing root coordinates:",
          int((observations["dns_lat"].isna() |
               observations["dns_lon"].isna()).sum()))

    observations = add_soi_columns(observations)
    summary = make_probe_summary(observations, target_day, loaded_slots)

    observations_dir = output_dir / "observations"
    summary_dir = output_dir / "summary"
    observations_dir.mkdir(parents=True, exist_ok=True)
    summary_dir.mkdir(parents=True, exist_ok=True)
    observations_path = observations_dir / f"{target_day}.parquet"
    summary_path = summary_dir / f"{target_day}.parquet"
    observations.to_parquet(observations_path, index=False, compression="zstd")
    summary.to_parquet(summary_path, index=False, compression="zstd")
    print(f"Wrote observations: {observations_path}")
    print(f"Wrote summary: {summary_path}")

    write_web_data(summary, observations, target_day, loaded_slots, args.site_dir)
    if args.csv:
        observations.to_csv(observations_dir / f"{target_day}.csv", index=False)
        summary.to_csv(summary_dir / f"{target_day}.csv", index=False)

    print("Violation summary:")
    for label, column in (
        ("0 km, >=1 letter", "violates_0km_1letter"),
        ("0 km, >=2 letters", "violates_0km_2letters"),
        ("100 km, >=1 letter", "violates_100km_1letter"),
        ("100 km, >=2 letters", "violates_100km_2letters"),
    ):
        print(f"  {label}: {int(summary[column].sum()):,} probes")
    print("Done.")


if __name__ == "__main__":
    main()
