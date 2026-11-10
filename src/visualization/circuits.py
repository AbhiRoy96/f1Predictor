"""Circuit visualization and layout geometry utilities."""

from typing import Dict, Optional
import numpy as np
import plotly.graph_objects as go
from src.utils.config import DEFAULT_COLOR


def create_circuit_map_figure(circuit_name: str) -> go.Figure:
    """Generate an interactive track layout figure for a given circuit."""
    fig = go.Figure()

    # Parametric synthetic track geometry based on circuit shape characteristics
    t = np.linspace(0, 2 * np.pi, 200)

    name_lower = circuit_name.lower()
    if "interlagos" in name_lower or "josé carlos pace" in name_lower or "brazil" in name_lower:
        # Anti-clockwise flowing track with Senna S, Curva do Sol, Junção
        x = (1.5 * np.sin(t) + 0.5 * np.sin(2 * t) - 0.2 * np.sin(4 * t)) * 1000
        y = (np.cos(t) + 0.4 * np.cos(3 * t) + 0.3 * np.sin(t)) * 1000
        turns = [
            {"name": "T1 (Senna 'S')", "x": x[15], "y": y[15]},
            {"name": "T4 (Curva do Sol)", "x": x[50], "y": y[50]},
            {"name": "T8 (Ferradura)", "x": x[100], "y": y[100]},
            {"name": "T12 (Junção)", "x": x[160], "y": y[160]},
        ]
    elif "monza" in name_lower:
        # High speed Monza layout
        x = (2.2 * np.sin(t) + 0.3 * np.sin(3 * t)) * 1000
        y = (0.7 * np.cos(t) + 0.2 * np.cos(2 * t)) * 1000
        turns = [
            {"name": "Prima Variante", "x": x[20], "y": y[20]},
            {"name": "Variante della Roggia", "x": x[70], "y": y[70]},
            {"name": "Variante Ascari", "x": x[130], "y": y[130]},
            {"name": "Curva Parabolica", "x": x[180], "y": y[180]},
        ]
    elif "silverstone" in name_lower:
        # Silverstone flowing layout
        x = (1.8 * np.sin(t) + 0.4 * np.cos(2 * t)) * 1000
        y = (1.2 * np.cos(t) - 0.3 * np.sin(3 * t)) * 1000
        turns = [
            {"name": "Copse", "x": x[30], "y": y[30]},
            {"name": "Maggotts & Becketts", "x": x[80], "y": y[80]},
            {"name": "Stowe", "x": x[140], "y": y[140]},
            {"name": "Club", "x": x[185], "y": y[185]},
        ]
    else:
        # Standard circuit loop
        x = (1.5 * np.sin(t) + 0.3 * np.sin(2 * t)) * 1000
        y = (np.cos(t) + 0.2 * np.cos(3 * t)) * 1000
        turns = [
            {"name": "Turn 1", "x": x[20], "y": y[20]},
            {"name": "Hairpin", "x": x[100], "y": y[100]},
            {"name": "Final Corner", "x": x[170], "y": y[170]},
        ]

    # Plot track trace
    fig.add_trace(go.Scatter(
        x=x,
        y=y,
        mode="lines",
        line=dict(color=DEFAULT_COLOR, width=5),
        name="Track Layout",
        hoverinfo="none",
    ))

    # Add Turn Markers
    for turn in turns:
        fig.add_trace(go.Scatter(
            x=[turn["x"]],
            y=[turn["y"]],
            mode="markers+text",
            marker=dict(size=9, color="#FFFFFF", line=dict(color="#E10600", width=2)),
            text=[turn["name"]],
            textposition="top center",
            textfont=dict(color="#CCCCCC", size=11),
            name=turn["name"],
            showlegend=False,
        ))

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(20,20,20,0.8)",
        margin=dict(l=20, r=20, t=30, b=20),
        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        yaxis=dict(showgrid=False, zeroline=False, showticklabels=False, scaleanchor="x", scaleratio=1),
        title=f"Circuit Layout: {circuit_name}",
    )
    return fig
