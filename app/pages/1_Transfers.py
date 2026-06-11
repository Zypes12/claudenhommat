import sys
import datetime
from pathlib import Path

import streamlit as st
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.data import load_csv
from logic.recommendations import (
    recommend_best_squad, recommend_transfers, get_transfer_schedule,
    fixture_difficulty, difficulty_label, expected_matchday_points,
    _enrich_with_team, BUDGET, MAX_TRANSFERS, POS_COLORS,
)

st.set_page_config(page_title="Transfers", page_icon="🔄", layout="wide")

st.title("🔄 Transfer Planner")
st.caption(f"Total transfer budget: **{MAX_TRANSFERS} substitutions** for the entire tournament.")

# ── Transfer counter ───────────────────────────────────────────────────────────
col_used, col_rem, col_prog = st.columns([1, 1, 2])
with col_used:
    used = st.number_input("Transfers used so far", min_value=0, max_value=MAX_TRANSFERS, value=0, step=1)
remaining = MAX_TRANSFERS - used
with col_rem:
    st.metric("Remaining", remaining,
              delta="⚠️ running low" if remaining < 10 else f"{remaining} left",
              delta_color="inverse" if remaining < 10 else "off")
with col_prog:
    st.markdown("<br>", unsafe_allow_html=True)
    st.progress(used / MAX_TRANSFERS, text=f"{used}/{MAX_TRANSFERS} used")

st.divider()

# ── Load data ──────────────────────────────────────────────────────────────────
players  = load_csv("players.csv")
fixtures = load_csv("fixtures.csv")
groups   = load_csv("groups.csv")
lineups  = load_csv("lineups.csv")

with st.spinner("Calculating…"):
    result = recommend_best_squad(players, fixtures, groups, lineups)

if result is None:
    st.warning("Not enough data. Check the **Data** page.")
    st.stop()

squad    = result["squad"]
enriched = result.get("enriched_players", _enrich_with_team(players, lineups))
today    = datetime.date.today().isoformat()

# ── Transfer schedule ──────────────────────────────────────────────────────────
st.markdown("### 📆 Transfer Windows")
st.caption(
    "The model spreads your remaining transfers across upcoming matchdays. "
    "Make your transfers **before the first game of each matchday**."
)

schedule = get_transfer_schedule(squad, enriched, fixtures, groups, used, today)

if schedule:
    for window in schedule:
        md       = window["matchday"]
        deadline = window["deadline_date"]
        days     = window["days_away"]
        urgency  = window["urgency"]
        budget_w = window["budget"]
        swaps    = window["swaps"]

        label = f"Matchday {md}" if str(md).replace(".","").isdigit() else str(md)
        days_label = "today" if days == 0 else f"in {days} day{'s' if days != 1 else ''}"

        with st.expander(
            f"{urgency}  **{label}** — deadline {deadline} ({days_label})"
            f"  ·  suggested transfers: {budget_w}",
            expanded=(days <= 3),
        ):
            if swaps:
                st.markdown("**Recommended swaps for this matchday:**")
                swap_rows = []
                for s in swaps:
                    swap_rows.append({
                        "OUT ↩️":    s["out"],
                        "IN ✅":     s["in"],
                        "Pos":       s["position"],
                        "Pts gain":  f"+{s['pts_gain']:.1f}",
                        "Why":       s["reason"],
                    })
                st.dataframe(
                    pd.DataFrame(swap_rows),
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.info("No clear transfer gains for this window — hold your transfers.")
else:
    st.info("No upcoming matchday windows found in the fixtures data.")

st.divider()

# ── Manual transfer explorer ───────────────────────────────────────────────────
st.markdown("### 🔍 Transfer Explorer")
st.caption("Browse alternatives for any position. Compare expected points vs. current squad.")

col_a, col_b = st.columns([1, 3])
pos_filter = col_a.radio("Position", ["All", "GK", "DEF", "MID", "FWD"], horizontal=False)
n = col_b.slider("Suggestions per side", 3, 10, 5)

pf = None if pos_filter == "All" else pos_filter
transfers = recommend_transfers(squad, enriched, fixtures, groups, n_suggestions=n, position_filter=pf)

col_out, col_in = st.columns(2)

with col_out:
    st.markdown("#### ↩️ Weakest in current squad")
    out_list = transfers.get("out", [])
    if out_list:
        out_df = pd.DataFrame(out_list)[["name", "position", "exp_pts", "team", "reason"]]
        out_df.columns = ["Player", "Pos", "Exp pts/g", "Team", "Notes"]
        st.dataframe(out_df, use_container_width=True, hide_index=True)
    else:
        st.info("No candidates for this filter.")

with col_in:
    st.markdown("#### ✅ Best available replacements")
    in_list = transfers.get("in", [])
    if in_list:
        in_df = pd.DataFrame(in_list)[["name", "position", "exp_pts", "team", "value", "reason"]]
        in_df.columns = ["Player", "Pos", "Exp pts/g", "Team", "Value", "Notes"]
        st.dataframe(in_df, use_container_width=True, hide_index=True)
    else:
        st.info("No candidates for this filter.")

st.divider()

# ── Fixture difficulty by matchday ────────────────────────────────────────────
st.markdown("### 📅 Squad Fixture Calendar")
st.caption("Colour = upcoming opponent difficulty. 🟢 Easy  🟡 Medium  🔴 Hard")

if not fixtures.empty and not groups.empty:
    unplayed = fixtures
    if "home_score" in fixtures.columns:
        unplayed = fixtures[
            fixtures["home_score"].isna() | (fixtures["home_score"].astype(str).str.strip() == "")
        ]

    rows = []
    for _, p in squad.iterrows():
        team = str(p.get("team", "")).strip()
        pos  = str(p.get("position", "")).upper()
        exp  = round(float(p.get("exp_pts", 0)), 1)

        team_fx = unplayed[
            (unplayed["home_team"].astype(str).str.strip() == team) |
            (unplayed["away_team"].astype(str).str.strip() == team)
        ].head(4)

        from logic.recommendations import get_team_ranking
        fx_cells: dict = {}
        for i, (_, fx) in enumerate(team_fx.iterrows(), 1):
            md_label = str(fx.get("matchday", "")).strip()
            md_label = f"MD{int(float(md_label))}" if md_label.replace(".","").isdigit() else "KO"
            opp = fx["away_team"] if str(fx["home_team"]).strip() == team else fx["home_team"]
            opp_rank = get_team_ranking(str(opp).strip(), groups)
            lbl, _ = difficulty_label(opp_rank)
            icon = "🟢" if lbl == "Easy" else ("🟡" if lbl == "Medium" else "🔴")
            fx_cells[md_label] = f"{icon} {opp}"

        row = {
            "Player": p.get("name", ""),
            "Pos":    pos,
            "Team":   team or "—",
            "Pts/g":  exp,
        }
        row.update(fx_cells)
        rows.append(row)

    if rows:
        cal_df = (
            pd.DataFrame(rows)
            .sort_values("Pts/g", ascending=False)
            .reset_index(drop=True)
        )
        st.dataframe(cal_df, use_container_width=True, hide_index=True)
else:
    st.info("Load Fixtures and Groups data first.")
