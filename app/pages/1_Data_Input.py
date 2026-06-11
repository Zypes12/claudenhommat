import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.data import load_csv, save_csv

st.set_page_config(page_title="Data Input", page_icon="📋", layout="wide")
st.title("📋 Data Input")
st.caption("Edit any table and press Save. Changes are written to the CSV files in Data/.")

tabs = st.tabs(["My Squad / Players", "Fixtures", "Groups", "Form", "Lineups", "Results"])

# ── Players / Squad ──────────────────────────────────────────────────────────
with tabs[0]:
    st.subheader("Players")
    st.caption(
        "Full player list. Tick **in_squad** for players in your current team, "
        "and **is_captain** for your captain. "
        "Penalty taker: No / Primary / Secondary. "
        "Set piece role: No / Free kicks / Corners / Penalties / Both."
    )
    df = load_csv("players.csv")
    edited = st.data_editor(
        df,
        num_rows="fixed",
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
            "in_squad": st.column_config.CheckboxColumn("In squad"),
            "is_captain": st.column_config.CheckboxColumn("Captain"),
            "value": st.column_config.TextColumn("Value"),
        },
    )
    if st.button("Save Players", key="save_players"):
        save_csv("players.csv", edited)
        st.success("Players saved!")

# ── Fixtures ──────────────────────────────────────────────────────────────────
with tabs[1]:
    st.subheader("Fixtures")
    st.caption(
        "Full match schedule. UK time (BST, GMT+1). "
        "Fill in **time_uk** as you go — format HH:MM, e.g. 20:00. "
        "Leave home_score / away_score blank for unplayed matches."
    )
    df = load_csv("fixtures.csv")
    edited = st.data_editor(
        df,
        num_rows="fixed",
        use_container_width=True,
        column_config={
            "match_id": st.column_config.NumberColumn("ID", disabled=True),
            "date": st.column_config.DateColumn("Date"),
            "stage": st.column_config.TextColumn("Stage"),
            "time_uk": st.column_config.TextColumn("Time (UK)"),
            "group": st.column_config.TextColumn("Group"),
        },
    )
    if st.button("Save Fixtures", key="save_fixtures"):
        save_csv("fixtures.csv", edited)
        st.success("Fixtures saved!")

# ── Groups ────────────────────────────────────────────────────────────────────
with tabs[2]:
    st.subheader("Groups & FIFA Rankings")
    st.caption("Team group assignments and FIFA rankings. Lower ranking number = stronger team.")
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
        st.success("Groups saved!")

# ── Form ──────────────────────────────────────────────────────────────────────
with tabs[3]:
    st.subheader("Team Form")
    st.caption(
        "Qualifying form per team. **last_10** = last 10 results string (W/D/L). "
        "Note: CONCACAF and AFC qualifying data is not yet included — "
        "Oceania (OFC) results involve weaker opposition."
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
        st.success("Form saved!")

# ── Lineups ───────────────────────────────────────────────────────────────────
with tabs[4]:
    st.subheader("Expected Lineups")
    st.caption("Starting XI per team with formation. Used for lineup status context.")
    df = load_csv("lineups.csv")
    edited = st.data_editor(
        df,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "position": st.column_config.TextColumn("Position"),
            "formation": st.column_config.TextColumn("Formation"),
        },
    )
    if st.button("Save Lineups", key="save_lineups"):
        save_csv("lineups.csv", edited)
        st.success("Lineups saved!")

# ── Results ───────────────────────────────────────────────────────────────────
with tabs[5]:
    st.subheader("Results & Goalscorers")
    st.caption(
        "Finished matches. Goalscorers as comma-separated names, "
        "e.g. 'Kane, Kane, Saka'."
    )
    df = load_csv("results.csv")
    edited = st.data_editor(
        df,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "date": st.column_config.DateColumn("Date"),
            "home_score": st.column_config.NumberColumn("Home score"),
            "away_score": st.column_config.NumberColumn("Away score"),
        },
    )
    if st.button("Save Results", key="save_results"):
        save_csv("results.csv", edited)
        st.success("Results saved!")
