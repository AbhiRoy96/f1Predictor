"""Screen 5 - Circuit Explorer."""

import streamlit as st
import pandas as pd
from src.data.normalise_entities import normalise_circuit
from src.enrichment.wikipedia_client import WikipediaClient
from src.visualization.circuits import create_circuit_map_figure

st.set_page_config(page_title="Circuit Explorer", page_icon="🏁", layout="wide")


def main():
    st.title("🏁 Circuit Explorer & Track Intelligence")
    st.markdown("---")

    wiki = WikipediaClient()
    circuits = [
        "Autódromo José Carlos Pace",
        "Silverstone Circuit",
        "Autodromo Nazionale Monza",
        "Circuit de Spa-Francorchamps",
        "Circuit de Monaco",
        "Suzuka International Racing Course",
        "Bahrain International Circuit",
    ]

    selected_circuit = st.selectbox("Select Circuit:", circuits)
    circuit_info = wiki.get_circuit_info(selected_circuit)

    col_map, col_details = st.columns([1, 1])
    with col_map:
        st.plotly_chart(create_circuit_map_figure(selected_circuit), use_container_width=True)

    with col_details:
        st.subheader(circuit_info.display_name)
        st.markdown(f"*{circuit_info.summary}*")
        st.caption(f"Source: {circuit_info.attribution}")

        st.markdown("#### Key Track Characteristics")
        c1, c2 = st.columns(2)
        with c1:
            st.metric("Track Length", "4.309 km", "71 Laps")
            st.metric("Elevation Change", "40.5 m", "Counter-Clockwise")
        with c2:
            st.metric("Number of Turns", "15 Corners", "2 DRS Zones")
            st.metric("Overtaking Difficulty", "Medium (0.45)", "High SC Likelihood")


if __name__ == "__main__":
    main()
