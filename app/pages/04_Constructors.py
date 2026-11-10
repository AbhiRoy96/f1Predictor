"""Screen 4 - Constructor Profiles."""

import streamlit as st
import pandas as pd
from src.data.normalise_entities import normalise_constructor
from src.enrichment.wikipedia_client import WikipediaClient
from src.utils.config import CONSTRUCTOR_COLORS

st.set_page_config(page_title="Constructor Profiles", page_icon="🏭", layout="wide")


def main():
    st.title("🏭 Constructor Profiles & Team Intelligence")
    st.markdown("---")

    wiki = WikipediaClient()
    teams = [
        "McLaren",
        "Ferrari",
        "Red Bull Racing",
        "Mercedes",
        "Aston Martin",
        "Alpine",
        "Williams",
        "RB",
        "Haas",
        "Kick Sauber",
    ]

    selected_team = st.selectbox("Select Constructor:", teams)
    team_info = wiki.get_constructor_info(selected_team)
    team_color = CONSTRUCTOR_COLORS.get(selected_team, "#E10600")

    col1, col2 = st.columns([1, 2])
    with col1:
        st.markdown(
            f"""
            <div style="background-color: {team_color}; padding: 30px; border-radius: 10px; text-align: center; color: white; font-weight: bold; font-size: 24px;">
                {selected_team}
            </div>
            """,
            unsafe_allow_html=True,
        )
        if team_info.image_url:
            st.image(team_info.image_url, width=280)
        st.caption(f"Source: {team_info.attribution}")

    with col2:
        st.subheader(team_info.display_name)
        st.markdown(f"*{team_info.summary}*")
        if team_info.wikipedia_url:
            st.markdown(f"[Wikipedia Team History]({team_info.wikipedia_url})")

    st.markdown("---")
    st.markdown("### 🏆 Season Performance & Driver Pairings")

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        st.metric("Constructors' Standing", "P1 (Leader)", delta="666 Pts")
    with col_b:
        st.metric("Season Wins", "8 Wins", delta="16 Podiums")
    with col_c:
        st.metric("Reliability Rate", "96.4%", delta="2 Mechanical DNFs")


if __name__ == "__main__":
    main()
