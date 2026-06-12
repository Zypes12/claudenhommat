import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.data import load_csv, save_csv

st.set_page_config(page_title="Data", layout="wide")
st.title("Data Management")
st.caption("Edit any table and press Save. Changes are written to the CSV files in Data/.")

tabs = st.tabs(["Players", "Fixtures", "Groups", "Form", "Lineups", "Results"])

# ── Players ────────────────────────────────────────────────────────────────────
with tabs[0]:
    st.subheader("Players")
    st.caption(
        "Full player list. **Penalty taker:** No / Primary / Secondary. "
        "**Set piece role:** No / Free kicks / Corners / Penalties / Both."
    )
    df = load_csv("players.csv")
    edited = st.data_editor(
        df,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "position": st.column_config.SelectboxColumn(
                "Position", options=["GK", "DEF", "MID", "FWD"]
            ),
            "penalties": st.column_config.SelectboxColumn(
                "Penalties", options=["Yes", "No"]
            ),
            "penalty_taker": st.column_config.SelectboxColumn(
                "Penalty taker", options=["No", "Primary", "Secondary"]
            ),
            "set_piece_role": st.column_config.SelectboxColumn(
                "Set piece role", options=["No", "Free kicks", "Corners", "Penalties", "Both"]
            ),
            "value": st.column_config.TextColumn("Value"),
        },
    )
    if st.button("Save Players", key="save_players"):
        save_csv("players.csv", edited)
        st.success("Saved.")

# ── Fixtures ───────────────────────────────────────────────────────────────────
with tabs[1]:
    st.subheader("Fixtures")
    st.caption("Match schedule. Fill **time_uk** as HH:MM (BST). Leave scores blank for unplayed matches.")
    df = load_csv("fixtures.csv")
    edited = st.data_editor(
        df,
        num_rows="fixed",
        use_container_width=True,
        column_config={
            "match_id":  st.column_config.NumberColumn("ID", disabled=True),
            "date":      st.column_config.TextColumn("Date"),
            "time_uk":   st.column_config.TextColumn("Time (UK)"),
            "stage":     st.column_config.TextColumn("Stage"),
            "group":     st.column_config.TextColumn("Group"),
        },
    )
    if st.button("Save Fixtures", key="save_fixtures"):
        save_csv("fixtures.csv", edited)
        st.success("Saved.")

# ── Groups ─────────────────────────────────────────────────────────────────────
with tabs[2]:
    st.subheader("Groups & FIFA Rankings")
    st.caption("Lower FIFA ranking number = stronger team.")
    df = load_csv("groups.csv")
    edited = st.data_editor(
        df,
        num_rows="fixed",
        use_container_width=True,
        column_config={
            "fifa_ranking": st.column_config.NumberColumn("FIFA Ranking"),
        },
    )
    if st.button("Save Groups", key="save_groups"):
        save_csv("groups.csv", edited)
        st.success("Saved.")

# ── Form ───────────────────────────────────────────────────────────────────────
with tabs[3]:
    st.subheader("Team Form")
    st.caption(
        "Qualifying form. **last_10** = last 10 results (W/D/L string). "
        "CONCACAF and AFC data not yet included."
    )
    df = load_csv("form.csv")
    edited = st.data_editor(
        df,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "region": st.column_config.SelectboxColumn(
                "Region", options=["UEFA", "CAF", "CONMEBOL", "OFC", "AFC", "CONCACAF"]
            ),
        },
    )
    if st.button("Save Form", key="save_form"):
        save_csv("form.csv", edited)
        st.success("Saved.")

# ── Lineups ────────────────────────────────────────────────────────────────────
with tabs[4]:
    st.subheader("Expected Lineups")
    st.caption("Starting XI per team. Used to assign players to teams for fixture scoring.")
    df = load_csv("lineups.csv")
    edited = st.data_editor(
        df,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "position":  st.column_config.TextColumn("Position"),
            "formation": st.column_config.TextColumn("Formation"),
        },
    )
    if st.button("Save Lineups", key="save_lineups"):
        save_csv("lineups.csv", edited)
        st.success("Saved.")

# ── Results ────────────────────────────────────────────────────────────────────
with tabs[5]:
    st.subheader("Results")
    st.caption("Finished matches. Goalscorers as comma-separated names, e.g. Kane, Kane, Saka.")
    df = load_csv("results.csv")
    edited = st.data_editor(
        df,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "date":       st.column_config.TextColumn("Date"),
            "home_score": st.column_config.NumberColumn("Home score"),
            "away_score": st.column_config.NumberColumn("Away score"),
        },
    )
    if st.button("Save Results", key="save_results"):
        save_csv("results.csv", edited)
        st.success("Saved.")
