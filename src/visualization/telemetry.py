"""Telemetry, pace, and prediction probability charts using Plotly."""

from typing import Dict, List, Optional
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from src.utils.config import CONSTRUCTOR_COLORS, DEFAULT_COLOR


def create_win_probability_chart(predictions_df: pd.DataFrame, max_drivers: int = 10) -> go.Figure:
    """Create a horizontal bar chart displaying driver win probabilities."""
    df = predictions_df.sort_values("win_probability", ascending=True).tail(max_drivers)

    colors = [
        CONSTRUCTOR_COLORS.get(row.get("TeamName", ""), DEFAULT_COLOR)
        for _, row in df.iterrows()
    ]

    fig = go.Figure(go.Bar(
        x=df["win_probability"] * 100,
        y=df["Abbreviation"],
        orientation="h",
        marker=dict(color=colors),
        text=(df["win_probability"] * 100).apply(lambda v: f"{v:.1f}%"),
        textposition="auto",
        hovertext=df.apply(lambda r: f"{r['Abbreviation']} ({r.get('TeamName', '')})", axis=1),
    ))

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(20,20,20,0.8)",
        margin=dict(l=10, r=20, t=30, b=20),
        xaxis=dict(title="Win Probability (%)", range=[0, max(df["win_probability"] * 100) * 1.15]),
        yaxis=dict(title="Driver"),
        title="Driver Win Probability Leaderboard",
    )
    return fig


def create_podium_probability_chart(predictions_df: pd.DataFrame, max_drivers: int = 10) -> go.Figure:
    """Create a bar chart displaying driver podium probabilities."""
    df = predictions_df.sort_values("podium_probability", ascending=False).head(max_drivers)

    colors = [
        CONSTRUCTOR_COLORS.get(row.get("TeamName", ""), "#FFD700")
        for _, row in df.iterrows()
    ]

    fig = go.Figure(go.Bar(
        x=df["Abbreviation"],
        y=df["podium_probability"] * 100,
        marker=dict(color=colors),
        text=(df["podium_probability"] * 100).apply(lambda v: f"{v:.1f}%"),
        textposition="auto",
    ))

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(20,20,20,0.8)",
        margin=dict(l=20, r=20, t=30, b=20),
        xaxis=dict(title="Driver"),
        yaxis=dict(title="Podium Probability (%)", range=[0, 100]),
        title="Podium Finish Likelihood (Top 3)",
    )
    return fig


def create_feature_importance_chart(feature_names: List[str], importances: List[float], top_n: int = 12) -> go.Figure:
    """Create a horizontal bar chart displaying top model feature importances."""
    df = pd.DataFrame({"feature": feature_names, "importance": importances})
    df = df.sort_values("importance", ascending=True).tail(top_n)

    fig = go.Figure(go.Bar(
        x=df["importance"],
        y=df["feature"],
        orientation="h",
        marker=dict(color="#00D2BE"),
        text=df["importance"].apply(lambda v: f"{v:.3f}"),
        textposition="auto",
    ))

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(20,20,20,0.8)",
        margin=dict(l=10, r=20, t=30, b=20),
        xaxis=dict(title="Relative Feature Importance"),
        yaxis=dict(title="Feature"),
        title=f"Top {top_n} Most Influential Prediction Factors",
    )
    return fig


def create_lap_time_distribution_chart(laps_df: pd.DataFrame) -> go.Figure:
    """Create box plots comparing lap times across drivers in weekend sessions."""
    fig = px.box(
        laps_df,
        x="Driver",
        y="LapTimeSeconds",
        color="Driver",
        template="plotly_dark",
        title="Practice & Qualifying Representative Pace Distribution",
    )
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(20,20,20,0.8)",
        margin=dict(l=20, r=20, t=30, b=20),
        xaxis=dict(title="Driver"),
        yaxis=dict(title="Lap Time (Seconds)"),
    )
    return fig
