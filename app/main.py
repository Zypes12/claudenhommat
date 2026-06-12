import datetime
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

from utils.data import load_csv
from utils.team_form import load_team_form, get_form_stats
from logic.recommendations import (
    recommend_best_squad, load_user_squad, squad_coverage_gaps,
    fixture_difficulty, difficulty_label,
    squad_fixture_table, display_name, compute_actual_stats, compute_recent_form,
    compute_group_standings, compute_advance_probability,
    BUDGET, CAPTAIN_MULTIPLIER, MAX_TRANSFERS, POS_COLORS, parse_value,
)

st.set_page_config(
    page_title="Futispörssi WC2026",
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
form       = load_csv("form.csv")
results    = load_csv("results.csv")
team_form  = load_team_form()
actual_stats  = compute_actual_stats(results)
recent_form   = compute_recent_form(results)
form_stats    = get_form_stats(team_form)
_standings    = compute_group_standings(results, groups)
advance_probs = {
    str(t): compute_advance_probability(str(t), _standings, groups)
    for t in groups["team"].astype(str).str.strip()
}

# ── Header ─────────────────────────────────────────────────────────────────────
st.title("Futispörssi · World Cup 2026")
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

# ── Load squad: user's actual squad first, fall back to algorithm ──────────────
with st.spinner("Loading squad…"):
    today_str = datetime.date.today().isoformat()
    shared_kwargs = dict(form=form, actual_stats=actual_stats, form_stats=form_stats, recent_form=recent_form)
    result = load_user_squad(players, lineups, fixtures, groups, today_str=today_str, **shared_kwargs)
    using_user_squad = result is not None
    if not using_user_squad:
        result = recommend_best_squad(players, fixtures, groups, lineups, **shared_kwargs)

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
# Compute total price change delta for squad (sum of change amounts from original prices)
_squad_value_delta = 0.0
for _, _pr in squad.iterrows():
    _pct_raw = str(_pr.get("value_change_pct", "")).strip()
    try:
        _pct = float(_pct_raw) if _pct_raw else 0.0
    except ValueError:
        _pct = 0.0
    if _pct != 0.0:
        _cur = parse_value(str(_pr.get("value", "0")))
        _orig = round(_cur / (1 + _pct / 100))
        _squad_value_delta += _cur - _orig

_delta_str = (
    f"▲{_squad_value_delta/1000:.0f}k vs original" if _squad_value_delta > 0 else
    f"▼{abs(_squad_value_delta)/1000:.0f}k vs original" if _squad_value_delta < 0 else
    "no price changes"
)

col_a, col_b, col_c, col_d = st.columns(4)
col_a.metric("Recommended Formation", formation)
col_b.metric(
    "Squad value",
    f"{budget_used / 1_000_000:.2f}M €",
    delta=_delta_str,
    delta_color="normal" if _squad_value_delta != 0 else "off",
)
col_c.metric("Total exp pts / game", f"{total_pts:.1f}")
col_d.metric("Captain", captain, delta=f"~{cap_pts:.1f} pts as captain", delta_color="off")

squad_label = "My Squad" if using_user_squad else "Recommended Starting XI"
st.markdown(f"### {squad_label}")

# ── Formation display ──────────────────────────────────────────────────────────

def _fmt_value(v) -> str:
    num = parse_value(str(v))
    if num >= 1_000_000:
        return f"{num / 1_000_000:.2f}M"
    if num >= 1_000:
        return f"{num / 1_000:.0f}k"
    return str(num)


def _card_html(p: dict, is_cap: bool) -> str:
    pos   = str(p.get("position", "")).upper()
    color = POS_COLORS.get(pos, "#7c3aed")
    name  = display_name(str(p.get("name", "—")))
    team  = p.get("team", "") or "—"
    pts   = float(p.get("exp_pts", 0))
    today_pts = float(p.get("today_pts", 0) or 0)
    bg    = "rgba(60,50,0,0.85)" if is_cap else "rgba(15,15,30,0.75)"
    ring  = "outline: 1.5px solid gold;" if is_cap else ""

    cap_badge = (
        '<div style="font-size:9px;color:gold;font-weight:700;letter-spacing:1px;'
        'margin-bottom:2px">★ CAPTAIN</div>'
    ) if is_cap else ""

    # Value display
    val_str = _fmt_value(p.get("value", "0"))

    # Price change badge
    pct_raw = str(p.get("value_change_pct", "")).strip()
    try:
        pct_val = float(pct_raw) if pct_raw else 0.0
    except ValueError:
        pct_val = 0.0

    if pct_val > 0:
        pct_badge = (
            f'<span style="color:#4ade80;font-size:9px;margin-left:3px">'
            f'▲{abs(pct_val):.0f}%</span>'
        )
    elif pct_val < 0:
        pct_badge = (
            f'<span style="color:#f87171;font-size:9px;margin-left:3px">'
            f'▼{abs(pct_val):.0f}%</span>'
        )
    else:
        pct_badge = ""

    # Points line: show today's pts prominently if playing today, else avg
    if today_pts > 0:
        pts_line = (
            f'<div style="font-size:12px;color:#fbbf24;margin-top:5px;font-weight:700">'
            f'today: {today_pts:.1f} pts</div>'
            f'<div style="font-size:10px;color:#7dd3fc">avg ~{pts:.1f}/g</div>'
        )
    else:
        pts_line = (
            f'<div style="font-size:12px;color:#7dd3fc;margin-top:5px;font-weight:600">'
            f'~{pts:.1f} pts/g</div>'
        )

    return (
        f'<div style="background:{bg};border-top:4px solid {color};{ring}'
        f'border-radius:10px;padding:12px 10px;text-align:center;'
        f'min-width:110px;max-width:150px;flex:1 1 0;">'
        f'<div style="font-size:9px;color:{color};font-weight:700;letter-spacing:1.5px">{pos}</div>'
        f'{cap_badge}'
        f'<div style="font-size:13px;font-weight:700;color:#f1f5f9;margin:4px 0 2px;line-height:1.2">{name}</div>'
        f'<div style="font-size:10px;color:#94a3b8">{team}</div>'
        f'<div style="font-size:11px;color:#cbd5e1;margin-top:3px">{val_str}{pct_badge}</div>'
        + pts_line +
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
    st.markdown("### Captain Pick")

    cc1, cc2, cc3, cc4 = st.columns(4)
    cc1.metric("Player", display_name(captain))
    cc2.metric("Position", str(cap.get("position", "")).upper())
    today_cap_pts = float(cap.get("today_pts", 0) or 0)
    if today_cap_pts > 0:
        cc3.metric("Exp pts today", f"{today_cap_pts:.1f}", delta="playing today", delta_color="off")
    else:
        cc3.metric("Exp pts / game (avg)", f"{float(cap.get('exp_pts', 0)):.1f}")
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
st.markdown("### Squad Fixture Breakdown")
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
