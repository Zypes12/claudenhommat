import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.data import load_csv
from logic.recommendations import (
    BUDGET, CAPTAIN_MULTIPLIER, SQUAD_SIZE, VALID_FORMATIONS,
    get_squad, squad_budget_used, validate_squad,
    recommend_captain, recommend_transfers, squad_fixture_summary,
)

st.set_page_config(page_title="Recommendations", page_icon="🎯", layout="wide")
st.title("🎯 Recommendations")
st.caption(
    "Scoring: GK goal +9, win +3, CS +3 | DEF goal +7, win +2, CS +2 | "
    "MID goal +5, win +1, CS +1 | FWD goal +4 | Captain ×1.3"
)

players  = load_csv("players.csv")
fixtures = load_csv("fixtures.csv")
groups   = load_csv("groups.csv")
lineups  = load_csv("lineups.csv")

squad = get_squad(players)

if players.empty:
    st.warning("No player data found. Check Data Input → Players.")
    st.stop()

# ── Squad status ──────────────────────────────────────────────────────────────
with st.expander("Squad status", expanded=squad.empty):
    errors = validate_squad(players)
    if errors:
        for e in errors:
            st.error(e)
    else:
        budget_used = squad_budget_used(squad)
        st.success(f"Valid squad: {len(squad)} players | Budget: {budget_used:,.0f} € / {BUDGET:,.0f} €")

    st.caption(
        f"Valid formations: {', '.join(VALID_FORMATIONS)}. "
        f"Squad size: {SQUAD_SIZE}. Budget: {BUDGET:,.0f} €."
    )

if squad.empty:
    st.warning("No squad set — go to Data Input → Players and tick in_squad for your 11 players.")
    st.stop()

st.divider()

# ── Captain ───────────────────────────────────────────────────────────────────
st.subheader("Recommended Captain")
st.caption(f"Captain scores ×{CAPTAIN_MULTIPLIER} (positive rounds up, negative rounds down).")

cap = recommend_captain(players, fixtures, groups)
if cap["player"]:
    col1, col2, col3 = st.columns(3)
    col1.metric("Captain pick", cap["player"])
    col2.metric("Expected pts (base)", cap.get("expected_pts", "?"))
    col3.metric(f"Expected pts (×{CAPTAIN_MULTIPLIER})", cap.get("captain_pts", "?"))
    st.info(cap["reason"])
else:
    st.info(cap["reason"])

st.divider()

# ── Transfer suggestions ───────────────────────────────────────────────────────
st.subheader("Transfer Suggestions")
st.caption(
    "Transfers are precious — max 50 for the whole tournament (35 base). "
    "Only consider transfers where the gain is clear."
)

col_a, col_b = st.columns([1, 3])
n = col_a.slider("Suggestions per position", 1, 5, 3)
pos_filter = col_b.selectbox(
    "Filter by position", ["All", "GK", "DEF", "MID", "FWD"]
)
pf = None if pos_filter == "All" else pos_filter

suggestions = recommend_transfers(players, fixtures, groups, lineups,
                                  n_suggestions=n, position_filter=pf)

if suggestions:
    for i, s in enumerate(suggestions, 1):
        with st.expander(
            f"{i}. **{s['name']}** ({s['position']}) — {s['value']} — "
            f"~{s['exp_pts']:.1f} pts/game"
        ):
            st.write(s["reason"])
else:
    st.info("No suggestions — check that Fixtures and Groups data are filled in.")

st.divider()

# ── Squad fixture + points breakdown ──────────────────────────────────────────
st.subheader("My Squad — Fixture & Points Breakdown")
st.caption(
    "Exp pts/game is an estimate based on upcoming fixture difficulty, "
    "position scoring rules, and set piece/penalty role. "
    "It does NOT know a player's personal goal-scoring history — "
    "use it as a relative ranking guide, not an absolute prediction."
)

summary = squad_fixture_summary(players, fixtures, groups)
if not summary.empty:
    st.dataframe(summary, use_container_width=True, hide_index=True)
else:
    st.info("Fill in Fixtures and Groups data to see the breakdown.")
