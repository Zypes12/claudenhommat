import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

from utils.data import load_csv
from logic.recommendations import get_squad

st.set_page_config(
    page_title="Futispörssi WC2026",
    page_icon="⚽",
    layout="wide",
)

st.title("⚽ Futispörssi World Cup 2026")
st.markdown("Use the **sidebar** to navigate between pages.")

st.divider()

players  = load_csv("players.csv")
fixtures = load_csv("fixtures.csv")
groups   = load_csv("groups.csv")
squad    = get_squad(players)

col1, col2, col3, col4 = st.columns(4)
col1.metric("Players in database", len(players))
col2.metric("My squad size", len(squad))
col3.metric("Fixtures loaded", len(fixtures[fixtures["stage"] == "Group Stage"]) if not fixtures.empty else 0)

captain_row = squad[squad["is_captain"].astype(str).str.lower().isin(["true", "1", "yes"])] if not squad.empty else None
if captain_row is not None and not captain_row.empty:
    col4.metric("Captain", captain_row.iloc[0]["name"])
else:
    col4.metric("Captain", "—")

st.subheader("My Squad")
if squad.empty:
    st.info("No squad set yet. Go to **Data Input** and tick **in_squad** for your 15 players.")
else:
    show_cols = [c for c in ["name", "position", "value", "penalties", "penalty_taker", "set_piece_role", "is_captain"] if c in squad.columns]
    st.dataframe(squad[show_cols], use_container_width=True, hide_index=True)
