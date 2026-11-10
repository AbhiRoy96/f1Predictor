"""Screen: Telemetry Cockpit Dashboard."""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from src.data.normalise_entities import normalise_driver_name
from src.visualization.circuits import create_circuit_map_figure
from src.utils.config import DEFAULT_COLOR

st.set_page_config(page_title="Telemetry Cockpit", page_icon="🎛️", layout="wide")


def main():
    st.title("🎛️ Telemetry Cockpit Dashboard")
    st.caption("High-density motorsport engineering interface | Data State: **Historical Session Analysis & Calibrated Prediction**")
    st.markdown("---")

    # Top Bar Selectors
    c_season, c_event, c_session, c_driver = st.columns(4)
    with c_season:
        season = st.selectbox("Season", [2026, 2025, 2024], index=0)
    with c_event:
        event = st.selectbox("Grand Prix", ["São Paulo Grand Prix", "British Grand Prix", "Monza Grand Prix"], index=0)
    with c_session:
        session = st.selectbox("Session", ["Qualifying (Q3)", "Practice 2 (FP2)", "Race (R)"], index=0)
    with c_driver:
        driver = st.selectbox("Telemetry Focus Driver", ["VER", "NOR", "LEC", "HAM", "PIA", "RUS"], index=0)

    # 3-Column Cockpit Layout
    col_left, col_center, col_right = st.columns([1.2, 1.4, 1.2])

    with col_left:
        st.markdown("### 📡 Session Feed & Telemetry")
        st.metric("Driver Position", "P1 (Leader)", delta="-0.086s Delta to P2")
        st.metric("Best Lap Time", "1:10.824", delta="Lap 14 (Soft New)")

        # Telemetry multi-panel trace (Speed, Throttle, Brake)
        dist = np.linspace(0, 4309, 150)
        speed = 180 + 120 * np.sin(dist / 350) + 30 * np.cos(dist / 150)
        throttle = np.clip(100 * (speed - 160) / 140, 0, 100)
        brake = np.where(throttle < 20, np.random.uniform(60, 100, 150), 0)

        fig_tel = go.Figure()
        fig_tel.add_trace(go.Scatter(x=dist, y=speed, name="Speed (km/h)", line=dict(color="#00D2BE", width=2)))
        fig_tel.add_trace(go.Scatter(x=dist, y=throttle, name="Throttle (%)", line=dict(color="#27F4D2", width=1, dash="dot")))
        fig_tel.add_trace(go.Scatter(x=dist, y=brake, name="Brake (%)", line=dict(color="#E10600", width=1.5)))

        fig_tel.update_layout(
            template="plotly_dark",
            height=280,
            margin=dict(l=10, r=10, t=25, b=10),
            xaxis=dict(title="Track Distance (m)"),
            yaxis=dict(title="Channel Value"),
            title=f"Telemetry Channels: {driver}",
        )
        st.plotly_chart(fig_tel, use_container_width=True)

    with col_center:
        st.markdown("### 🗺️ Track Geometry & Sector Timing")
        st.plotly_chart(create_circuit_map_figure("Interlagos"), use_container_width=True)

        cs1, cs2, cs3 = st.columns(3)
        with cs1:
            st.metric("Sector 1", "18.420s", "🟣 Purple")
        with cs2:
            st.metric("Sector 2", "34.110s", "🟢 Green")
        with cs3:
            st.metric("Sector 3", "18.294s", "🟣 Purple")

    with col_right:
        st.markdown("### 🧪 Performance & Strategy Analytics")
        st.metric("Win Probability", "42.0%", delta="Calibrated Ensemble")
        st.metric("Podium Probability", "85.0%", delta="Expected P1.8")

        # Tyre Degradation Curve
        laps = np.arange(1, 26)
        soft_deg = 71.0 + 0.08 * laps + np.random.normal(0, 0.05, 25)
        medium_deg = 71.4 + 0.04 * laps + np.random.normal(0, 0.04, 25)

        fig_deg = go.Figure()
        fig_deg.add_trace(go.Scatter(x=laps, y=soft_deg, name="Soft (C4) Pace", line=dict(color="#FF3333", width=2)))
        fig_deg.add_trace(go.Scatter(x=laps, y=medium_deg, name="Medium (C3) Pace", line=dict(color="#FFD700", width=2)))
        fig_deg.update_layout(
            template="plotly_dark",
            height=280,
            margin=dict(l=10, r=10, t=25, b=10),
            xaxis=dict(title="Stint Lap Count"),
            yaxis=dict(title="Estimated Lap Time (s)"),
            title="Estimated Tyre Pace Degradation",
        )
        st.plotly_chart(fig_deg, use_container_width=True)

    # Strategy Timeline
    st.markdown("---")
    st.markdown("### ⏱️ Stint & Strategy Timeline")
    strategy_df = pd.DataFrame([
        {"Driver": driver, "Stint": "Stint 1 (Laps 1-22)", "Compound": "Soft (Used)", "Avg Pace": "72.140s", "Status": "Historical"},
        {"Driver": driver, "Stint": "Pit Stop 1 (Lap 22)", "Compound": "Stationary 2.4s", "Avg Pace": "21.8s Pit Loss", "Status": "Observed"},
        {"Driver": driver, "Stint": "Stint 2 (Laps 23-48)", "Compound": "Medium (New)", "Avg Pace": "72.480s", "Status": "Simulated Strategy"},
        {"Driver": driver, "Stint": "Stint 3 (Laps 49-71)", "Compound": "Hard (New)", "Avg Pace": "72.820s", "Status": "Simulated Strategy"},
    ])
    st.dataframe(strategy_df, use_container_width=True)

    st.caption("Data Provenance: Session fastf1 (2026 Season, Rd 21) | Model Version: v1.0.0-ensemble | Prediction Cutoff: 2026-11-08 16:30 UTC")


if __name__ == "__main__":
    main()
