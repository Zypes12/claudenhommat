import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

from utils.data import load_csv
from logic.recommendations import (
    recommend_best_squad, fixture_difficulty, difficulty_label,
    squad_fixture_table, display_name,
    BUDGET, CAPTAIN_MULTIPLIER, MAX_TRANSFERS, POS_COLORS,
)

st.set_page_config(
    page_title="Futispörssi WC2026",
    page_icon="⚽",
    layout="wide",
)

# ── Styles ─────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
.pitch-wrap {
    background: linear-gradient(180deg, #0a3d1f 0%, #0d5c2e 40%, #0d5c2e 60%, #0a3d1f 100%);
    border-radius: 16px;
    padding: 24px 16px;
    margin: 8px 0 16px 0;
    border: 2px solid #1a6b35;
}
.pitch-row {
    display: flex;
    justify-content: center;
    align-items: stretch;
    gap: 10px;
    margin: 10px 0;
}
.pitch-divider {
    border: none;
    border-top: 1px dashed rgba(255,255,255,0.15);
    margin: 4px 40px;
}
</style>
""", unsafe_allow_html=True)

# ── Load data ──────────────────────────────────────────────────────────────────
players  = load_csv("players.csv")
fixtures = load_csv("fixtures.csv")
groups   = load_csv("groups.csv")
lineups  = load_csv("lineups.csv")

# ── Header ─────────────────────────────────────────────────────────────────────
st.title("⚽ Futispörssi  ·  World Cup 2026")
st.caption("Squad and captain recommendations based on fixture difficulty, set-piece roles, and scoring rules.")
st.divider()

# ── Top metrics ────────────────────────────────────────────────────────────────
total_fixtures = len(fixtures) if not fixtures.empty else 0
played = 0
if not fixtures.empty and "home_score" in fixtures.columns:
    played = int(fixtures["home_score"].notna().sum())
remaining = total_fixtures - played

c1, c2, c3, c4 = st.columns(4)
c1.metric("Players in database", f"{len(players):,}")
c2.metric("Teams tracked", groups["team"].nunique() if not groups.empty else 0)
c3.metric("Fixtures remaining", remaining)
c4.metric("Transfers budget", f"35 total")

st.divider()

# ── Generate recommendation ────────────────────────────────────────────────────
with st.spinner("Calculating best squad…"):
    result = recommend_best_squad(players, fixtures, groups, lineups)

if result is None:
    st.warning(
        "Not enough data to generate a squad recommendation. "
        "Make sure Fixtures, Groups, and Players data are loaded on the **Data** page."
    )
    st.stop()

squad      = result["squad"]
formation  = result["formation"]
captain    = result["captain"]
cap_pts    = result["captain_pts"]
total_pts  = result["total_pts"]
budget_used = result["budget_used"]
budget_left = BUDGET - budget_used

# ── Squad summary bar ──────────────────────────────────────────────────────────
col_a, col_b, col_c, col_d = st.columns(4)
col_a.metric("Recommended Formation", formation)
col_b.metric("Budget used", f"{budget_used / 1_000_000:.2f}M €", delta=f"{budget_left / 1_000:.0f}k remaining", delta_color="off")
col_c.metric("Total exp pts / game", f"{total_pts:.1f}")
col_d.metric("Captain", captain, delta=f"~{cap_pts:.1f} pts as captain", delta_color="off")

st.markdown("### Starting XI")

# ── Formation display ──────────────────────────────────────────────────────────

def _card_html(p: dict, is_cap: bool) -> str:
    pos   = str(p.get("position", "")).upper()
    color = POS_COLORS.get(pos, "#7c3aed")
    name  = display_name(str(p.get("name", "—")))
    team  = p.get("team", "") or "—"
    pts   = float(p.get("exp_pts", 0))
    bg    = "rgba(60,50,0,0.85)" if is_cap else "rgba(15,15,30,0.75)"
    ring  = f"outline: 1.5px solid gold;" if is_cap else ""
    cap_badge = (
        '<div style="font-size:9px;color:gold;font-weight:700;letter-spacing:1px;'
        'margin-bottom:2px">★ CAPTAIN</div>'
    ) if is_cap else ""
    return (
        f'<div style="background:{bg};border-top:4px solid {color};{ring}'
        f'border-radius:10px;padding:12px 10px;text-align:center;'
        f'min-width:110px;max-width:150px;flex:1 1 0;">'
        f'<div style="font-size:9px;color:{color};font-weight:700;letter-spacing:1.5px">{pos}</div>'
        f'{cap_badge}'
        f'<div style="font-size:13px;font-weight:700;color:#f1f5f9;margin:4px 0 2px;line-height:1.2">{name}</div>'
        f'<div style="font-size:10px;color:#94a3b8">{team}</div>'
        f'<div style="font-size:12px;color:#7dd3fc;margin-top:5px;font-weight:600">~{pts:.1f} pts/g</div>'
        f'</div>'
    )


def _row_html(player_list: list[dict]) -> str:
    cards = "".join(_card_html(p, p.get("name") == captain) for p in player_list)
    return (
        '<div class="pitch-row">'
        + cards +
        '</div>'
    )


# Build the full pitch HTML in one block (avoids Streamlit column squishing)
to_records = lambda pos: squad[squad["position"].str.upper() == pos].to_dict("records")
fwd_list = to_records("FWD")
mid_list = to_records("MID")
def_list = to_records("DEF")
gk_list  = to_records("GK")

pitch_html = (
    '<div class="pitch-wrap">'
    + _row_html(fwd_list)
    + '<hr class="pitch-divider">'
    + _row_html(mid_list)
    + '<hr class="pitch-divider">'
    + _row_html(def_list)
    + '<hr class="pitch-divider">'
    + _row_html(gk_list)
    + '</div>'
)
st.markdown(pitch_html, unsafe_allow_html=True)

st.divider()

# ── Captain reasoning ──────────────────────────────────────────────────────────
cap_row = squad[squad["name"] == captain]
if not cap_row.empty:
    cap = cap_row.iloc[0]
    st.markdown("### ⭐ Captain Pick")

    cc1, cc2, cc3, cc4 = st.columns(4)
    cc1.metric("Player", display_name(captain))
    cc2.metric("Position", str(cap.get("position", "")).upper())
    cc3.metric("Exp pts / game", f"{float(cap.get('exp_pts', 0)):.1f}")
    cc4.metric(f"As captain (×{CAPTAIN_MULTIPLIER})", f"{cap_pts:.1f}")

    reasons = []
    pt = str(cap.get("penalty_taker", "")).lower()
    if pt in ("primary", "secondary"):
        reasons.append(f"Penalty taker ({pt})")
    spr = str(cap.get("set_piece_role", "")).lower()
    if spr not in ("no", "none", ""):
        reasons.append(f"Set pieces: {spr}")
    team = str(cap.get("team", "")).strip()
    if team:
        diff = fixture_difficulty(team, fixtures, groups)
        lbl, _ = difficulty_label(diff)
        reasons.append(f"{lbl} upcoming fixtures (avg opp. rank {diff:.0f})")
    if reasons:
        st.info("  ·  ".join(reasons))

st.divider()

# ── Fixture difficulty table ───────────────────────────────────────────────────
st.markdown("### 📅 Squad Fixture Breakdown")
ft = squad_fixture_table(squad, fixtures, groups, next_n=4)
if not ft.empty:
    def color_difficulty(val):
        colors = {"Easy": "#166534", "Medium": "#854d0e", "Hard": "#7f1d1d"}
        bg = colors.get(val, "")
        return f"background-color: {bg}; color: white; border-radius: 4px; padding: 2px 6px;" if bg else ""

    styled = ft.style.map(color_difficulty, subset=["Fixtures"])
    st.dataframe(styled, use_container_width=True, hide_index=True)
else:
    st.info("Fill in Fixtures and Groups data to see the breakdown.")
