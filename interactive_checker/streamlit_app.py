from pathlib import Path

import pandas as pd
import streamlit as st


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
SUMMARY_DIR = DATA_DIR / "summary"
OBSERVATIONS_DIR = DATA_DIR / "observations"


st.set_page_config(
    page_title="RIPE Atlas SOI Checker",
    page_icon="🌐",
    layout="wide",
)


@st.cache_data(show_spinner=False)
def read_parquet(path_str: str) -> pd.DataFrame:
    """Read a parquet file and cache it until the file changes/redeploys."""
    return pd.read_parquet(path_str)


@st.cache_data(show_spinner=False)
def available_summary_dates() -> list[pd.Timestamp]:
    """Return dates for which a daily summary parquet exists."""
    if not SUMMARY_DIR.exists():
        return []

    dates = []
    for path in SUMMARY_DIR.glob("*.parquet"):
        try:
            dates.append(pd.Timestamp(path.stem).normalize())
        except ValueError:
            continue

    return sorted(set(dates))


def summary_path(day: pd.Timestamp) -> Path:
    return SUMMARY_DIR / f"{day.strftime('%Y-%m-%d')}.parquet"


def observations_path(day: pd.Timestamp) -> Path:
    return OBSERVATIONS_DIR / f"{day.strftime('%Y-%m-%d')}.parquet"


def load_summaries(days: list[pd.Timestamp]) -> pd.DataFrame:
    frames = []

    for day in days:
        path = summary_path(day)
        if not path.exists():
            continue

        frame = read_parquet(str(path)).copy()

        if "date" not in frame.columns:
            frame["date"] = day.strftime("%Y-%m-%d")

        frame["date"] = pd.to_datetime(frame["date"]).dt.date
        frames.append(frame)

    if not frames:
        return pd.DataFrame()

    return pd.concat(frames, ignore_index=True)


def load_probe_observations(
    probe_id: int,
    days: list[pd.Timestamp],
) -> pd.DataFrame:
    frames = []

    for day in days:
        path = observations_path(day)
        if not path.exists():
            continue

        frame = read_parquet(str(path))
        frame = frame.loc[frame["probe_id"] == probe_id].copy()

        if frame.empty:
            continue

        if "date" not in frame.columns:
            frame["date"] = day.strftime("%Y-%m-%d")

        frame["date"] = pd.to_datetime(frame["date"]).dt.date
        frames.append(frame)

    if not frames:
        return pd.DataFrame()

    return pd.concat(frames, ignore_index=True)


def selected_columns(use_buffer: bool, two_letters: bool) -> tuple[str, str, int]:
    buffer_key = "100km" if use_buffer else "0km"
    letters_key = "2letters" if two_letters else "1letter"
    min_letters = 2 if two_letters else 1

    summary_col = f"violates_{buffer_key}_{letters_key}"
    observation_col = f"violates_{buffer_key}"

    return summary_col, observation_col, min_letters


def format_float(value, digits=1):
    if pd.isna(value):
        return "—"
    return f"{value:,.{digits}f}"


st.title("RIPE Atlas SOI Checker")
st.caption(
    "Check whether a RIPE Atlas probe's reported location is inconsistent "
    "with latency to root-server instances. Daily results use the minimum "
    "RTT observed across the scheduled 06:00, 12:00, and 18:00 UTC snapshots "
    "available for that day."
)

all_dates = available_summary_dates()

if not all_dates:
    st.error(
        "No processed RIPE Atlas data is available yet. "
        "Run the data-update workflow first."
    )
    st.stop()

latest_date = max(all_dates)

with st.sidebar:
    st.header("Checker settings")

    days = st.number_input(
        "Look back (days)",
        min_value=1,
        max_value=30,
        value=1,
        step=1,
        help="1 day is the default. The window ends on the newest available data date.",
    )

    use_buffer = st.checkbox(
        "100 km tolerance",
        value=True,
        help="Paper default. Turn off to use a 0 km tolerance.",
    )

    two_letters = st.checkbox(
        "Require ≥2 root letters",
        value=True,
        help="Paper default. Turn off to flag a probe when one root letter violates.",
    )

    st.divider()
    st.caption(f"Newest data: {latest_date.strftime('%Y-%m-%d')} UTC")

window_start = latest_date - pd.Timedelta(days=int(days) - 1)
selected_dates = [day for day in all_dates if window_start <= day <= latest_date]

summary_df = load_summaries(selected_dates)

search_col, observation_violation_col, min_letters = selected_columns(
    use_buffer=use_buffer,
    two_letters=two_letters,
)

if search_col not in summary_df.columns:
    st.error(
        f"The processed data is missing `{search_col}`. "
        "Re-run the current fetch_data.py workflow."
    )
    st.stop()

left, right = st.columns([3, 1])

with left:
    probe_text = st.text_input(
        "RIPE Atlas probe ID",
        placeholder="e.g. 10001",
    )

with right:
    st.write("")
    st.write("")
    check = st.button(
        "Check probe",
        type="primary",
        use_container_width=True,
    )

if check:
    try:
        probe_id = int(probe_text.strip())
        if probe_id < 1:
            raise ValueError
    except (ValueError, AttributeError):
        st.warning("Enter a valid RIPE Atlas probe ID.")
        st.stop()
    probe_summary = summary_df.loc[summary_df["probe_id"] == probe_id].copy()

    if probe_summary.empty:
        st.warning(
            f"Probe {probe_id} does not appear in the processed data for "
            f"{window_start.strftime('%Y-%m-%d')} through "
            f"{latest_date.strftime('%Y-%m-%d')} UTC."
        )
        st.stop()

    probe_summary = probe_summary.sort_values("date", ascending=False)
    violating_days = probe_summary.loc[probe_summary[search_col].fillna(False)]
    violates = not violating_days.empty

    if violates:
        st.error(
            f"SOI violation detected for probe {probe_id} "
            f"on {len(violating_days)} day(s) in the selected window."
        )
    else:
        st.success(
            f"No SOI violation detected for probe {probe_id} "
            "in the selected window."
        )

    # Probe metadata from the newest available summary row.
    newest = probe_summary.iloc[0]

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Days with data", len(probe_summary))
    m2.metric("Violating days", len(violating_days))

    roots_observed = newest.get("num_roots_observed", pd.NA)
    if pd.isna(roots_observed):
        m3.metric("Roots observed", "—")
    else:
        m3.metric("Roots observed", int(roots_observed))

    slots = newest.get("snapshot_slots_loaded", "—")
    if pd.isna(slots):
        slots = "—"
    else:
        slots = str(slots).replace(",", ", ")
    m4.metric("Latest day's snapshots", slots)

    metadata_bits = []
    country = newest.get("country")
    asn_v4 = newest.get("asn_v4")

    if pd.notna(country):
        metadata_bits.append(f"country: **{country}**")
    if pd.notna(asn_v4):
        try:
            metadata_bits.append(f"IPv4 ASN: **AS{int(asn_v4)}**")
        except (TypeError, ValueError):
            metadata_bits.append(f"IPv4 ASN: **{asn_v4}**")

    if metadata_bits:
        st.caption(" · ".join(metadata_bits))

    if not violates:
        with st.expander("Show daily results"):
            daily_display = probe_summary[
                [
                    c
                    for c in [
                        "date",
                        "num_violating_letters_0km",
                        "num_violating_letters_100km",
                        "snapshot_slots_loaded",
                    ]
                    if c in probe_summary.columns
                ]
            ].copy()

            daily_display = daily_display.rename(
                columns={
                    "date": "Date",
                    "num_violating_letters_0km": "Violating letters (0 km)",
                    "num_violating_letters_100km": "Violating letters (100 km)",
                    "snapshot_slots_loaded": "Snapshots used",
                }
            )

            st.dataframe(
                daily_display,
                use_container_width=True,
                hide_index=True,
            )

        st.stop()

    violating_dates = {
        pd.Timestamp(day).normalize()
        for day in violating_days["date"]
    }

    observations = load_probe_observations(
        probe_id=probe_id,
        days=sorted(violating_dates),
    )

    if observations.empty:
        st.warning(
            "The probe is marked as violating, but its observation detail "
            "file could not be loaded."
        )
        st.stop()

    if observation_violation_col not in observations.columns:
        st.error(
            f"Observation data is missing `{observation_violation_col}`."
        )
        st.stop()

    # Only show roots that violate the selected distance threshold, and only
    # on days that satisfy the selected one-/two-letter rule.
    details = observations.loc[
        observations[observation_violation_col].fillna(False)
    ].copy()

    details["violation_margin_km"] = details["excess_km"] - (
        100 if use_buffer else 0
    )

    st.subheader("Where does it violate?")

    violating_letters = sorted(
        details["root_letter"].dropna().astype(str).str.upper().unique()
    )

    d1, d2, d3 = st.columns(3)
    d1.metric(
        "Violating root letters",
        ", ".join(violating_letters) if violating_letters else "—",
    )
    d2.metric(
        "Largest violation margin",
        (
            f"{details['violation_margin_km'].max():,.0f} km"
            if not details.empty
            else "—"
        ),
    )
    d3.metric(
        "Rule applied",
        f"≥{min_letters} letter{'s' if min_letters > 1 else ''}, "
        f"{'100 km' if use_buffer else '0 km'} buffer",
    )

    table = details.copy()
    table["root_letter"] = table["root_letter"].astype(str).str.upper()

    wanted = [
        "date",
        "root_letter",
        "root_ns",
        "hostname",
        "min_rtt_slot_utc",
        "rtt_ms",
        "haversine_distance_km",
        "furthest_possible_km",
        "violation_margin_km",
        "dns_lat",
        "dns_lon",
    ]
    wanted = [column for column in wanted if column in table.columns]

    table = table[wanted].sort_values(
        [c for c in ["date", "violation_margin_km"] if c in wanted],
        ascending=False,
    )

    table = table.rename(
        columns={
            "date": "Date",
            "root_letter": "Root",
            "root_ns": "Root instance",
            "hostname": "hostname.bind",
            "min_rtt_slot_utc": "Min RTT snapshot (UTC)",
            "rtt_ms": "RTT (ms)",
            "haversine_distance_km": "Distance (km)",
            "furthest_possible_km": "Max feasible (km)",
            "violation_margin_km": "Beyond threshold (km)",
            "dns_lat": "Root latitude",
            "dns_lon": "Root longitude",
        }
    )

    numeric_cols = [
        "RTT (ms)",
        "Distance (km)",
        "Max feasible (km)",
        "Beyond threshold (km)",
    ]
    for col in numeric_cols:
        if col in table.columns:
            table[col] = table[col].round(1)

    st.dataframe(
        table,
        use_container_width=True,
        hide_index=True,
    )

    # Map the claimed probe location plus the violating root-instance locations.
    map_rows = []

    probe_lat = newest.get("latitude")
    probe_lon = newest.get("longitude")

    if pd.notna(probe_lat) and pd.notna(probe_lon):
        map_rows.append(
            {
                "lat": float(probe_lat),
                "lon": float(probe_lon),
            }
        )

    if {"dns_lat", "dns_lon"}.issubset(details.columns):
        root_points = (
            details[["dns_lat", "dns_lon"]]
            .dropna()
            .drop_duplicates()
        )

        for _, row in root_points.iterrows():
            map_rows.append(
                {
                    "lat": float(row["dns_lat"]),
                    "lon": float(row["dns_lon"]),
                }
            )

    if map_rows:
        st.subheader("Probe and violating root-instance locations")
        st.caption(
            "The map includes the probe's reported location and the root "
            "instances implicated by the selected rule."
        )
        st.map(pd.DataFrame(map_rows))

    with st.expander("Show daily rule counts"):
        daily_display = probe_summary[
            [
                c
                for c in [
                    "date",
                    "num_violating_letters_0km",
                    "num_violating_letters_100km",
                    "num_roots_observed",
                    "snapshot_slots_loaded",
                ]
                if c in probe_summary.columns
            ]
        ].copy()

        daily_display = daily_display.rename(
            columns={
                "date": "Date",
                "num_violating_letters_0km": "Violating letters (0 km)",
                "num_violating_letters_100km": "Violating letters (100 km)",
                "num_roots_observed": "Roots observed",
                "snapshot_slots_loaded": "Snapshots used",
            }
        )

        st.dataframe(
            daily_display,
            use_container_width=True,
            hide_index=True,
        )

st.divider()
st.caption(
    "Methodology: for each probe/root-letter/day, the checker uses the "
    "minimum RTT among the scheduled snapshots available that day. The "
    "maximum feasible distance is RTT/2 × 199,862.638 km/s."
)
