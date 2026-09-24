import numpy as np
import pandas as pd

SOI_SPEED_KM_S = 199_862.638
EARTH_RADIUS_KM = 6371.0


def furthest_possible_distance(rtt_ms):
    """
    Maximum one-way geographic path implied by RTT,
    assuming propagation at the SOI speed.
    """
    return (SOI_SPEED_KM_S * (rtt_ms / 1000.0)) / 2


def haversine_distance(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(
        np.radians, [lat1, lon1, lat2, lon2]
    )

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        np.sin(dlat / 2) ** 2
        + np.cos(lat1)
        * np.cos(lat2)
        * np.sin(dlon / 2) ** 2
    )

    return EARTH_RADIUS_KM * (
        2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))
    )


def evaluate_soi(df, buffer_km=100, min_letters=2):
    df = df.copy()

    # Claimed probe location -> observed root instance location
    df["haversine_distance_km"] = haversine_distance(
        df["latitude"],
        df["longitude"],
        df["dns_lat"],
        df["dns_lon"],
    )

    # RTT/2 * propagation speed
    df["furthest_possible_km"] = furthest_possible_distance(
        df["rtt_ms"]
    )

    # Positive means the target is farther away than physically
    # feasible under the selected tolerance.
    df["violation_margin_km"] = (
        df["haversine_distance_km"]
        - df["furthest_possible_km"]
        - buffer_km
    )

    df["violates_single_letter"] = (
        df["violation_margin_km"] > 0
    )

    # Count DISTINCT violating root letters at each snapshot.
    letter_counts = (
        df.loc[df["violates_single_letter"]]
        .groupby(["probe_id", "snapshot"])["root_letter"]
        .nunique()
        .rename("num_violating_letters")
        .reset_index()
    )

    df = df.merge(
        letter_counts,
        on=["probe_id", "snapshot"],
        how="left",
    )

    df["num_violating_letters"] = (
        df["num_violating_letters"]
        .fillna(0)
        .astype(int)
    )

    df["violates_soi"] = (
        df["num_violating_letters"] >= min_letters
    )

    return df
