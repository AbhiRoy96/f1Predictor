"""Screen 2 - Race Weekend Analytics."""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from src.visualization.telemetry import create_lap_time_distribution_chart

st.set_page_config(page_title="Race Weekend Analytics", page_icon="⏱️", layout="wide")


def main():
    st.title("⏱️ Race Weekend Analytics")
    st.subheader("2026 São Paulo Grand Prix — Practice & Qualifying Intelligence")
    st.markdown("---")

    tab1, tab2, tab3 = st.tabs(["🏎️ Lap Times & Pace", "🎯 Qualifying Gaps", "🌦️ Weather & Race Control"])

    with tab1:
        st.markdown("### FP1, FP2 & Sprint Shootout Clean Lap Distributions")
        np.random.seed(42)
        drivers = ["VER", "NOR", "LEC", "PIA", "HAM", "RUS", "ALO", "SAI", "TSU", "ALB"]
        laps_data = []
        base_times = [71.2, 71.3, 71.5, 71.6, 71.8, 71.9, 72.2, 72.1, 72.5, 72.6]

        for d, base in zip(drivers, base_times):
            times = np.random.normal(base, 0.4, 25)
            for t in times:
                laps_data.append({"Driver": d, "LapTimeSeconds": round(t, 3)})

        laps_df = pd.DataFrame(laps_data)
        st.plotly_chart(create_lap_time_distribution_chart(laps_df), use_container_width=True)

    with tab2:
        st.markdown("### Gap to Pole Position (Qualifying)")
        quali_data = [
            {"Driver": "VER", "Team": "Red Bull", "Q3_Time": 70.824, "GapToPole": 0.000},
            {"Driver": "NOR", "Team": "McLaren", "Q3_Time": 70.910, "GapToPole": 0.086},
            {"Driver": "LEC", "Team": "Ferrari", "Q3_Time": 71.012, "GapToPole": 0.188},
            {"Driver": "PIA", "Team": "McLaren", "Q3_Time": 71.140, "GapToPole": 0.316},
            {"Driver": "HAM", "Team": "Ferrari", "Q3_Time": 71.205, "GapToPole": 0.381},
            {"Driver": "RUS", "Team": "Mercedes", "Q3_Time": 71.290, "GapToPole": 0.466},
        ]
        q_df = pd.DataFrame(quali_data)
        fig_q = px.bar(
            q_df,
            x="Driver",
            y="GapToPole",
            color="Team",
            template="plotly_dark",
            title="Gap to Pole (Seconds)",
            text=q_df["GapToPole"].apply(lambda v: f"+{v:.3f}s" if v > 0 else "POLE"),
        )
        st.plotly_chart(fig_q, use_container_width=True)

    with tab3:
        st.markdown("### Pre-Race Weather Snapshots & Track Temperature")
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Air Temperature", "23.4 °C", delta="-1.2 °C")
        with col2:
            st.metric("Track Temperature", "38.2 °C", delta="+3.5 °C")
        with col3:
            st.metric("Rain Risk", "35%", delta="Overcast clouds")

        st.markdown("#### Race Control Timeline (Earlier Weekend Sessions)")
        st.info("🟡 FP1 (12:15 UTC): Yellow flag Turn 4 - Stroll off track (Turn cleared)")
        st.info("🔴 Sprint Qualifying (16:40 UTC): Red flag Turn 1 - Colapinto incident (Barrier repair completed)")


if __name__ == "__main__":
    main()
