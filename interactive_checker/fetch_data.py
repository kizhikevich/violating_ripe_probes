#!/usr/bin/env python3

import argparse
import bz2
import json
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import ijson
import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


API_URL = "https://atlas.ripe.net/api/v2/measurements"

META_LATEST_URL = (
    "https://ftp.ripe.net/ripe/atlas/probes/archive/meta-latest"
)

META_ARCHIVE_URL = (
    "https://ftp.ripe.net/ripe/atlas/probes/archive/"
    "{year}/{month:02d}/{datestr}.json.bz2"
)

SOI_SPEED_KM_S = 199_862.638
EARTH_RADIUS_KM = 6371.0


# hostname.bind measurement IDs, IPv4.
# IPv6 IDs are IPv4 ID + 1000.
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


# Your existing hostname -> site parsing rules.
regexes = {
    "a": [
        re.compile(r"^(?:rootns-|nnn1-)([a-z]{3})\d+$"),
        re.compile(r"^(?:rootns-|nnn1-)el([a-z]{3})\d+$"),
        re.compile(
            r"^(?:rootns-|nnn1-)(?:[a-z]{2})([a-z]{3})-\d+[a-z]?$"
        ),
    ],
    "b": re.compile(r"^b\d-([a-z]{3})$"),
    "c": re.compile(
        r"^([a-z]{3})\d[a-z]\.c\.root-servers\.org$"
    ),
    "d": re.compile(
        r"^([a-z]{4})\d\.droot\.maxgigapop\.net$"
    ),
    "e": re.compile(
        r"^(?:[a-z]\d+)\.([a-z]{3})[a-z0-9]?\.eroot$"
    ),
    "f": re.compile(
        r"^([a-z]{3})(?:\d[a-z]|\.cf)\.f\.root-servers\.org$"
    ),
    "g": re.compile(
        r"^groot-?-(.*?)-.*?(\.net)?$"
    ),
    "h": re.compile(
        r"^\d+\.([a-z]{3})\.h\.root-servers\.org$"
    ),
    "i": re.compile(
        r"^s\d\.([a-z]{3})$"
    ),
    "j": [
        re.compile(r"^(?:rootns-|nnn1-)([a-z]{3})\d+$"),
        re.compile(r"^(?:rootns-|nnn1-)el([a-z]{3})\d+$"),
        re.compile(
            r"^(?:rootns-|nnn1-)(?:[a-z]{2})([a-z]{3})-\d+[a-z]?$"
        ),
    ],
    "k": re.compile(
        r"^.*?\.([a-z]{2}-[a-z]{3})\.k\.ripe\.net$"
    ),
    "l": re.compile(
        r"^([a-z]{2}-[a-z]{3})-[a-z]{2}$"
    ),
    "m": re.compile(
        r"^m-([a-z]{3})(-[a-z]+)?-\d$"
    ),
}


def make_session():
    """
    requests Session with retry/backoff for transient RIPE/API failures.
    """
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

    session.headers.update({
        "User-Agent": "ripe-atlas-soi-checker/1.0"
    })

    return session


def normalize_hostname(hostname):
    if hostname is None:
        return None

    hostname = str(hostname).strip()
    hostname = hostname.strip("\"'")
    hostname = hostname.rstrip(".")
    hostname = hostname.lower()

    return hostname or None


def parse_site(letter, hostname):
    """
    Extract root site code from hostname using regex map.
    """
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


def extract_hostname(result):
    """
    Extract hostname.bind TXT response from a RIPE Atlas DNS result.

    Typically:

        result["answers"][0]["RDATA"] == ["b3-ams"]

    but this handles either a list or a single string.
    """
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


def fetch_daily_minima(
    session,
    letter,
    start_dt,
    stop_dt,
    proto=4,
):
    """
    Fetch one day's hostname.bind results for one root letter.

    The API response is streamed with ijson so the complete raw day's
    results never need to live in memory.

    Returns one row per probe: the VALID/PARSEABLE observation with the
    minimum RTT for that probe/root-letter/day.
    """
    measurement_id = measurements[letter]

    if proto == 6:
        measurement_id += 1000

    url = f"{API_URL}/{measurement_id}/results/"

    params = {
        "start": start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stop": stop_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    print(
        f"Fetching {letter.upper()} root "
        f"({measurement_id})..."
    )

    response = session.get(
        url,
        params=params,
        stream=True,
        timeout=(20, 300),
    )

    response.raise_for_status()

    # Let urllib3 transparently decompress gzip responses while ijson
    # consumes the stream.
    response.raw.decode_content = True

    best_by_probe = {}
    stats = Counter()

    for obj in ijson.items(response.raw, "item"):
        stats["total"] += 1

        probe_id = obj.get("prb_id")
        timestamp = obj.get("timestamp")
        dns_result = obj.get("result")

        if probe_id is None:
            stats["missing_probe_id"] += 1
            continue

        if timestamp is None:
            stats["missing_timestamp"] += 1
            continue

        if not isinstance(dns_result, dict):
            stats["missing_dns_result"] += 1
            continue

        rtt = dns_result.get("rt")

        if rtt is None:
            stats["missing_rtt"] += 1
            continue

        try:
            rtt = float(rtt)
        except (TypeError, ValueError):
            stats["invalid_rtt"] += 1
            continue

        if not np.isfinite(rtt) or rtt < 0:
            stats["invalid_rtt"] += 1
            continue

        hostname = extract_hostname(dns_result)

        if hostname is None:
            stats["missing_hostname"] += 1
            continue

        site = parse_site(letter, hostname)

        if site is None:
            stats["unparsed_hostname"] += 1
            continue

        stats["usable"] += 1

        row = {
            "probe_id": int(probe_id),
            "root_letter": letter,
            "hostname": hostname,
            "site": site,
            "rtt_ms": rtt,
            "timestamp": int(timestamp),
            "measurement_id": measurement_id,
        }

        previous = best_by_probe.get(int(probe_id))

        if previous is None or rtt < previous["rtt_ms"]:
            best_by_probe[int(probe_id)] = row

    response.close()

    stats["selected_probes"] = len(best_by_probe)

    print(
        f"  {letter.upper()}: "
        f"{stats['total']:,} results, "
        f"{stats['usable']:,} usable, "
        f"{stats['selected_probes']:,} probes, "
        f"{stats['unparsed_hostname']:,} unparsed hostnames"
    )

    return list(best_by_probe.values()), stats


def fetch_probe_metadata(session, target_day):
    """
    Fetch probe metadata.

    Prefer the archived metadata snapshot for the requested date so
    historical runs are reproducible. Fall back to meta-latest if that
    day's archive is not available.
    """
    datestr = target_day.strftime("%Y%m%d")

    archive_url = META_ARCHIVE_URL.format(
        year=target_day.year,
        month=target_day.month,
        datestr=datestr,
    )

    urls = [
        archive_url,
        META_LATEST_URL,
    ]

    meta_json = None
    used_url = None

    for url in urls:
        print(f"Trying probe metadata: {url}")

        response = session.get(
            url,
            timeout=(20, 120),
        )

        if response.status_code == 404:
            print("  Not available; trying fallback.")
            continue

        response.raise_for_status()

        try:
            decompressed = bz2.decompress(response.content)
            meta_json = json.loads(decompressed)
            used_url = url
            break

        except (OSError, json.JSONDecodeError) as exc:
            print(
                f"  Could not decode metadata: {exc}"
            )

    if meta_json is None:
        raise RuntimeError(
            "Unable to download usable RIPE Atlas probe metadata."
        )

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

    print(
        f"Loaded {len(df):,} probe metadata records "
        f"from {used_url}"
    )

    return df


def load_root_locations(path):
    """
    Optional mapping from parsed root site codes to coordinates.

    Expected columns:

        root_letter
        site
        dns_lat
        dns_lon

    Optional extra columns such as dns_city/dns_country are preserved.
    """
    path = Path(path)

    if not path.exists():
        print(
            f"No root-location file at {path}. "
            "Skipping dns_lat/dns_lon enrichment."
        )
        return None

    locations = pd.read_csv(path)

    required = {
        "root_letter",
        "site",
        "dns_lat",
        "dns_lon",
    }

    missing = required - set(locations.columns)

    if missing:
        raise ValueError(
            f"{path} is missing required columns: "
            f"{sorted(missing)}"
        )

    locations["root_letter"] = (
        locations["root_letter"]
        .astype(str)
        .str.lower()
        .str.strip()
    )

    locations["site"] = (
        locations["site"]
        .astype(str)
        .str.lower()
        .str.strip()
    )

    # A root_letter/site pair must identify exactly one location.
    duplicate_keys = locations.duplicated(
        ["root_letter", "site"],
        keep=False,
    )

    if duplicate_keys.any():
        duplicates = locations.loc[
            duplicate_keys,
            ["root_letter", "site"],
        ].drop_duplicates()

        raise ValueError(
            "root_locations.csv contains duplicate "
            "root_letter/site mappings:\n"
            f"{duplicates.to_string(index=False)}"
        )

    print(
        f"Loaded {len(locations):,} root-site locations "
        f"from {path}"
    )

    return locations


def furthest_possible_distance(rtt_ms):
    """
    Maximum one-way distance under SOI:

        RTT / 2 * propagation speed
    """
    return (
        SOI_SPEED_KM_S
        * (rtt_ms / 1000.0)
        / 2.0
    )


def haversine_distance(
    lat1,
    lon1,
    lat2,
    lon2,
):
    lat1, lon1, lat2, lon2 = map(
        np.radians,
        [lat1, lon1, lat2, lon2],
    )

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        np.sin(dlat / 2.0) ** 2
        + np.cos(lat1)
        * np.cos(lat2)
        * np.sin(dlon / 2.0) ** 2
    )

    # Protect against tiny floating-point excursions above 1.
    a = np.clip(a, 0, 1)

    return EARTH_RADIUS_KM * (
        2
        * np.arctan2(
            np.sqrt(a),
            np.sqrt(1 - a),
        )
    )


def add_soi_geometry(df):
    """
    Precompute quantities needed by the web app.

    excess_km > 0   -> violation with no buffer
    excess_km > 100 -> violation with 100 km buffer
    """
    df = df.copy()

    df["furthest_possible_km"] = (
        furthest_possible_distance(df["rtt_ms"])
    )

    if not {
        "dns_lat",
        "dns_lon",
    }.issubset(df.columns):
        return df

    df["haversine_distance_km"] = np.nan

    valid = (
        df["latitude"].notna()
        & df["longitude"].notna()
        & df["dns_lat"].notna()
        & df["dns_lon"].notna()
    )

    df.loc[
        valid,
        "haversine_distance_km",
    ] = haversine_distance(
        df.loc[valid, "latitude"],
        df.loc[valid, "longitude"],
        df.loc[valid, "dns_lat"],
        df.loc[valid, "dns_lon"],
    )

    df["excess_km"] = (
        df["haversine_distance_km"]
        - df["furthest_possible_km"]
    )

    return df


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Fetch one UTC day of RIPE Atlas hostname.bind "
            "measurements and retain the minimum RTT for each "
            "probe/root-letter pair."
        )
    )

    parser.add_argument(
        "--date",
        help=(
            "UTC date to fetch, YYYY-MM-DD. "
            "Default: previous complete UTC day."
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
        help=(
            "Optional CSV mapping root_letter/site to "
            "dns_lat/dns_lon. Default: root_locations.csv"
        ),
    )

    parser.add_argument(
        "--csv",
        action="store_true",
        help="Also write a CSV copy in addition to Parquet.",
    )

    return parser.parse_args()


def main():
    args = parse_args()

    if args.date:
        target_day = datetime.strptime(
            args.date,
            "%Y-%m-%d",
        ).date()
    else:
        target_day = (
            datetime.now(timezone.utc).date()
            - timedelta(days=1)
        )

    start_dt = datetime(
        target_day.year,
        target_day.month,
        target_day.day,
        tzinfo=timezone.utc,
    )

    stop_dt = start_dt + timedelta(days=1)

    print()
    print("=" * 60)
    print("RIPE Atlas SOI daily fetch")
    print("=" * 60)
    print(f"UTC day: {target_day}")
    print(f"Start:   {start_dt.isoformat()}")
    print(f"Stop:    {stop_dt.isoformat()}")
    print()

    session = make_session()

    all_rows = []
    stats_by_letter = {}

    #
    # 1. Fetch all 13 root letters.
    #
    for letter in measurements:
        rows, stats = fetch_daily_minima(
            session=session,
            letter=letter,
            start_dt=start_dt,
            stop_dt=stop_dt,
            proto=4,
        )

        all_rows.extend(rows)
        stats_by_letter[letter] = stats

    rtt_df = pd.DataFrame(all_rows)

    if rtt_df.empty:
        raise RuntimeError(
            "No usable RIPE Atlas results were returned."
        )

    #
    # Sanity check:
    # There should be at most one row for each
    # probe_id/root_letter pair.
    #
    duplicates = rtt_df.duplicated(
        ["probe_id", "root_letter"]
    )

    if duplicates.any():
        raise RuntimeError(
            "Unexpected duplicate probe/root-letter rows "
            "after minimum-RTT reduction."
        )

    rtt_df["date"] = target_day.isoformat()

    rtt_df["timestamp_utc"] = pd.to_datetime(
        rtt_df["timestamp"],
        unit="s",
        utc=True,
    )

    print()
    print(
        f"Daily reduction produced "
        f"{len(rtt_df):,} probe/root rows "
        f"for {rtt_df['probe_id'].nunique():,} probes."
    )

    #
    # 2. Add RIPE Atlas probe metadata.
    #
    meta_df = fetch_probe_metadata(
        session,
        target_day,
    )

    merged = rtt_df.merge(
        meta_df,
        on="probe_id",
        how="left",
        validate="many_to_one",
    )

    missing_probe_locations = (
        merged["latitude"].isna()
        | merged["longitude"].isna()
    ).sum()

    print(
        f"Rows missing probe coordinates: "
        f"{missing_probe_locations:,}"
    )

    #
    # 3. Optionally add root-instance coordinates.
    #
    root_locations = load_root_locations(
        args.root_locations
    )

    if root_locations is not None:
        merged = merged.merge(
            root_locations,
            on=["root_letter", "site"],
            how="left",
            validate="many_to_one",
        )

        missing_root_locations = (
            merged["dns_lat"].isna()
            | merged["dns_lon"].isna()
        ).sum()

        print(
            f"Rows missing root-site coordinates: "
            f"{missing_root_locations:,}"
        )

    #
    # 4. Precompute SOI quantities.
    #
    merged = add_soi_geometry(merged)

    #
    # Put the important columns first.
    #
    preferred_order = [
        "date",
        "probe_id",
        "root_letter",
        "rtt_ms",
        "timestamp",
        "timestamp_utc",
        "hostname",
        "site",
        "measurement_id",
        "latitude",
        "longitude",
        "country",
        "asn_v4",
        "asn_v6",
        "status",
        "dns_lat",
        "dns_lon",
        "furthest_possible_km",
        "haversine_distance_km",
        "excess_km",
    ]

    columns = [
        c for c in preferred_order
        if c in merged.columns
    ]

    columns += [
        c for c in merged.columns
        if c not in columns
    ]

    merged = merged[columns]

    #
    # 5. Save one file per day.
    #
    output_dir = Path(args.output_dir)
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    parquet_path = (
        output_dir
        / f"{target_day.isoformat()}.parquet"
    )

    merged.to_parquet(
        parquet_path,
        index=False,
        compression="zstd",
    )

    print()
    print(f"Wrote {parquet_path}")
    print(
        f"Parquet size: "
        f"{parquet_path.stat().st_size / 1024 / 1024:.2f} MB"
    )

    if args.csv:
        csv_path = (
            output_dir
            / f"{target_day.isoformat()}.csv"
        )

        merged.to_csv(
            csv_path,
            index=False,
        )

        print(f"Wrote {csv_path}")

    #
    # Summary of hostname parsing.
    #
    print()
    print("Hostname parsing summary:")

    for letter in measurements:
        stats = stats_by_letter[letter]

        print(
            f"  {letter.upper()}: "
            f"{stats['selected_probes']:,} probes selected; "
            f"{stats['unparsed_hostname']:,} "
            f"unparsed observations"
        )

    print()
    print("Done.")


if __name__ == "__main__":
    main()
