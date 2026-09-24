import streamlit as st

from soi import evaluate_soi
from data import load_probe_data

st.set_page_config(
    page_title="RIPE Atlas SOI Checker",
    page_icon="🌐",
    layout="wide",
)

st.title("RIPE Atlas SOI Checker")
st.caption(
    "Check whether a RIPE Atlas probe's reported location "
    "is inconsistent with its observed latency to root-server instances."
)

c1, c2, c3 = st.columns([2, 1, 1])

with c1:
    probe_id = st.number_input(
        "Probe ID",
        min_value=1,
        step=1,
    )

with c2:
    days = st.number_input(
        "Look back (days)",
        min_value=1,
        max_value=30,
        value=1,
    )

with c3:
    st.write("")
    st.write("")
    check = st.button(
        "Check probe",
        type="primary",
        use_container_width=True,
    )

settings1, settings2 = st.columns(2)

with settings1:
    use_buffer = st.toggle(
        "100 km tolerance",
        value=True,
        help="Paper default",
    )

with settings2:
    two_letters = st.toggle(
        "Require ≥2 root letters",
        value=True,
        help="Paper default",
    )


if check:
    with st.spinner(f"Checking probe {probe_id}..."):
        df = load_probe_data(
            probe_id=int(probe_id),
            days=int(days),
        )

        result = evaluate_soi(
            df,
            buffer_km=100 if use_buffer else 0,
            min_letters=2 if two_letters else 1,
        )

    violating_snapshots = (
        result.groupby("snapshot")["violates_soi"]
        .any()
    )

    violating = violating_snapshots.any()

    if violating:
        st.error("SOI violation detected")
    else:
        st.success(
            "No SOI violation detected in this window"
        )

    a, b, c = st.columns(3)

    a.metric(
        "Violating snapshots",
        f"{violating_snapshots.sum()} / {len(violating_snapshots)}",
    )

    bad = result[
        result["violates_soi"]
        & result["violates_single_letter"]
    ]

    b.metric(
        "Root letters",
        ", ".join(sorted(bad["root_letter"].unique()))
        if len(bad)
        else "—",
    )

    c.metric(
        "Largest violation",
        (
            f"{bad['violation_margin_km'].max():,.0f} km"
            if len(bad)
            else "—"
        ),
    )

    if len(bad):
        st.subheader("Where does it violate?")

        display = bad[
            [
                "snapshot",
                "root_letter",
                "rtt_ms",
                "haversine_distance_km",
                "furthest_possible_km",
                "violation_margin_km",
                "dns_lat",
                "dns_lon",
            ]
        ].sort_values("snapshot", ascending=False)

        st.dataframe(
            display,
            use_container_width=True,
            hide_index=True,
        )
