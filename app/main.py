import datetime
import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

from utils.data import load_csv, save_csv, load_transfer_count, save_transfer_count, record_transfer, get_last_transfer, undo_last_transfer
from utils.team_form import load_team_form, get_form_stats
from logic.recommendations import (
    recommend_best_squad, load_user_squad, squad_coverage_gaps,
    fixture_difficulty, difficulty_label,
    squad_fixture_table, display_name, compute_actual_stats, compute_recent_form,
    compute_group_standings, compute_advance_probability,
    recommend_transfers,
    BUDGET, CAPTAIN_MULTIPLIER, MAX_TRANSFERS, POS_COLORS, parse_value,
    expected_matchday_points,
)

st.set_page_config(
    page_title="Futispörssi WC2026",
    layout="wide",
)

# ── Styles ─────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* Tighter page padding */
.main .block-container {
    padding-top: 0.75rem !important;
    padding-bottom: 0.5rem !important;
    padding-left: 1.5rem !important;
    padding-right: 1.5rem !important;
    max-width: 100% !important;
}
/* Smaller headings */
h1 { font-size: 1.4rem !important; margin-bottom: 0.4rem !important; }
h2 { font-size: 1.15rem !important; margin-bottom: 0.3rem !important; }
h3 { font-size: 1rem !important; margin-bottom: 0.25rem !important; }
/* Tighter metric cards */
[data-testid="metric-container"] {
    padding: 0.4rem 0.6rem !important;
}
[data-testid="stMetricValue"] { font-size: 1.1rem !important; }
[data-testid="stMetricLabel"] { font-size: 0.7rem !important; }
/* Pitch */
.pitch-wrap {
    background: linear-gradient(180deg, #0a3d1f 0%, #0d5c2e 40%, #0d5c2e 60%, #0a3d1f 100%);
    border-radius: 12px;
    padding: 12px 8px;
    margin: 4px 0 10px 0;
    border: 2px solid #1a6b35;
    position: relative;
    overflow: hidden;
}
.pitch-row {
    display: flex;
    justify-content: center;
    align-items: flex-start;
    gap: 6px;
    margin: 6px 0;
}
.pitch-divider {
    border: none;
    border-top: 1px dashed rgba(255,255,255,0.15);
    margin: 2px 30px;
}
/* Sidebar nav — button style + capitalize */
[data-testid="stSidebarNav"] a {
    border-radius: 6px !important;
    margin: 2px 0 !important;
    padding: 6px 12px !important;
    display: block !important;
    text-transform: capitalize !important;
    background: rgba(255,255,255,0.05) !important;
    border: 1px solid rgba(255,255,255,0.08) !important;
    transition: background 0.15s;
}
[data-testid="stSidebarNav"] a:hover {
    background: rgba(255,255,255,0.12) !important;
}
[data-testid="stSidebarNav"] a[aria-current="page"] {
    background: rgba(99,102,241,0.2) !important;
    border-color: rgba(99,102,241,0.45) !important;
}
/* Sidebar chat */
section[data-testid="stSidebar"] > div {
    padding-top: 0.75rem;
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
st.title("Futispörssi · WC 2026")

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

_transfers_used = load_transfer_count()
_transfers_left = MAX_TRANSFERS - _transfers_used

col_a, col_b, col_c, col_d = st.columns(4)
col_a.metric(
    "Transfers remaining",
    f"{_transfers_left} / {MAX_TRANSFERS}",
    delta="running low" if _transfers_left < 8 else None,
    delta_color="inverse",
)
col_b.metric(
    "Budget left",
    f"{budget_left / 1_000_000:.2f}M €",
)
col_c.metric(
    "Squad value",
    f"{budget_used / 1_000_000:.2f}M €",
    delta=_delta_str,
    delta_color="normal" if _squad_value_delta != 0 else "off",
)
col_d.metric("Captain pick", captain, delta=f"~{cap_pts:.1f} pts as captain", delta_color="off")

# ── Cancel last transfer ───────────────────────────────────────────────────────
_last_xfer = get_last_transfer()
if _last_xfer:
    _undo_out = display_name(str(_last_xfer.get("out", "")))
    _undo_in  = display_name(str(_last_xfer.get("in", "")))
    _undo_col, _ = st.columns([3, 7])
    with _undo_col:
        if st.button(f"↩ Undo: {_undo_in} → {_undo_out}", key="undo_xfer"):
            _entry = undo_last_transfer()
            if _entry:
                _undo_df = load_csv("players.csv")
                _undo_df.loc[
                    _undo_df["name"].astype(str).str.strip() == _entry["in"], "in_squad"
                ] = "False"
                _undo_df.loc[
                    _undo_df["name"].astype(str).str.strip() == _entry["out"], "in_squad"
                ] = "True"
                save_csv("players.csv", _undo_df)
                for _k in list(st.session_state.keys()):
                    del st.session_state[_k]
                st.rerun()

squad_label = "My Squad" if using_user_squad else "Recommended Starting XI"
st.markdown(f"### {squad_label}")

# ── Formation display ──────────────────────────────────────────────────────────

# Pre-compute next fixture label per team for card display
_team_next_game: dict = {}
for _t in squad["team"].astype(str).str.strip().unique():
    if not _t or _t in ("nan", ""):
        continue
    _up = fixtures[
        ((fixtures["home_team"].astype(str).str.strip() == _t) |
         (fixtures["away_team"].astype(str).str.strip() == _t)) &
        (fixtures["date"].astype(str).str.strip() >= today_str)
    ].sort_values("date")
    if not _up.empty:
        _fx = _up.iloc[0]
        _opp = (str(_fx["away_team"]).strip()
                if str(_fx["home_team"]).strip() == _t
                else str(_fx["home_team"]).strip())
        try:
            _d_fmt = datetime.date.fromisoformat(str(_fx["date"]).strip()).strftime("%-d %b")
        except Exception:
            _d_fmt = str(_fx["date"]).strip()
        _team_next_game[_t] = f"{_d_fmt} vs {_opp}"


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
    next_game = _team_next_game.get(str(team).strip(), "")
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
            f'<div style="font-size:10px;color:#fbbf24;margin-top:3px;font-weight:700">'
            f'today: {today_pts:.1f} pts</div>'
            f'<div style="font-size:9px;color:#7dd3fc">avg ~{pts:.1f}/g</div>'
        )
    else:
        pts_line = (
            f'<div style="font-size:10px;color:#7dd3fc;margin-top:3px;font-weight:600">'
            f'~{pts:.1f} pts/g</div>'
        )

    return (
        f'<div style="background:{bg};border-top:3px solid {color};{ring}'
        f'border-radius:8px;padding:7px 5px;text-align:center;'
        f'width:110px;flex:0 0 110px;">'
        f'<div style="font-size:8px;color:{color};font-weight:700;letter-spacing:1px">{pos}</div>'
        f'{cap_badge}'
        f'<div style="font-size:11px;font-weight:700;color:#f1f5f9;margin:3px 0 1px;line-height:1.2">{name}</div>'
        f'<div style="font-size:9px;color:#94a3b8">{team}</div>'
        f'<div style="font-size:10px;color:#cbd5e1;margin-top:2px">{val_str}{pct_badge}</div>'
        + pts_line
        + (f'<div style="font-size:8px;color:#fbbf24;margin-top:3px;opacity:0.9">{next_game}</div>'
           if next_game else "")
        + f'</div>'
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

_pitch_lines_svg = """
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" preserveAspectRatio="none"
     style="position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none;">
  <!-- Outer boundary -->
  <rect x="3" y="2" width="94" height="96" fill="none" stroke="white" stroke-width="0.5" opacity="0.18"/>
  <!-- Halfway line -->
  <line x1="3" y1="50" x2="97" y2="50" stroke="white" stroke-width="0.5" opacity="0.22"/>
  <!-- Centre circle -->
  <circle cx="50" cy="50" r="14" fill="none" stroke="white" stroke-width="0.5" opacity="0.18"/>
  <!-- Centre spot -->
  <circle cx="50" cy="50" r="1.2" fill="white" opacity="0.22"/>
  <!-- Top penalty box -->
  <rect x="22" y="2" width="56" height="22" fill="none" stroke="white" stroke-width="0.45" opacity="0.15"/>
  <!-- Top 6-yard box -->
  <rect x="36" y="2" width="28" height="9" fill="none" stroke="white" stroke-width="0.35" opacity="0.12"/>
  <!-- Top penalty spot -->
  <circle cx="50" cy="16" r="0.9" fill="white" opacity="0.15"/>
  <!-- Bottom penalty box -->
  <rect x="22" y="76" width="56" height="22" fill="none" stroke="white" stroke-width="0.45" opacity="0.15"/>
  <!-- Bottom 6-yard box -->
  <rect x="36" y="89" width="28" height="9" fill="none" stroke="white" stroke-width="0.35" opacity="0.12"/>
  <!-- Bottom penalty spot -->
  <circle cx="50" cy="84" r="0.9" fill="white" opacity="0.15"/>
</svg>
"""

pitch_html = (
    '<div class="pitch-wrap">'
    + _pitch_lines_svg
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
    cc1.markdown(
        f"<div style='font-size:12px;color:#94a3b8;margin-bottom:4px'>Player</div>"
        f"<div style='font-size:15px;font-weight:700;color:#f1f5f9;line-height:1.3'>"
        f"{display_name(captain)}</div>",
        unsafe_allow_html=True,
    )
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

# ── Recommended Transfers ──────────────────────────────────────────────────────
st.markdown("### Recommended Transfers")

if _transfers_left <= 0:
    st.warning("No transfers remaining.")
else:
    def _next_game_date(team: str) -> str:
        if not team or fixtures.empty:
            return ""
        up = fixtures[
            ((fixtures["home_team"].astype(str).str.strip() == team) |
             (fixtures["away_team"].astype(str).str.strip() == team)) &
            (fixtures["date"].astype(str).str.strip() >= today_str)
        ].sort_values("date")
        if up.empty:
            return ""
        row = up.iloc[0]
        opp = (str(row["away_team"]).strip()
               if str(row["home_team"]).strip() == team
               else str(row["home_team"]).strip())
        return f"{str(row['date']).strip()} vs {opp}"

    with st.spinner("Computing recommendations…"):
        _recs_raw = recommend_transfers(
            squad, players, fixtures, groups,
            n_suggestions=5,
            today_str=today_str,
            **shared_kwargs,
        )
        # Pair each OUT candidate with the best available IN of the same position
        _squad_names_set = set(squad["name"].astype(str).str.strip())
        _used_in_names: set[str] = set()
        _recs = []
        for _o in _recs_raw.get("out", []):
            if len(_recs) >= 3:
                break
            _out_pos = str(_o.get("position", "")).upper()
            _pos_pool = players[
                (~players["name"].astype(str).str.strip().isin(_squad_names_set)) &
                (~players["name"].astype(str).str.strip().isin(_used_in_names)) &
                (players["position"].astype(str).str.upper() == _out_pos)
            ].copy()
            if _pos_pool.empty:
                continue
            _pos_pool["_pts"] = _pos_pool.apply(
                lambda r: expected_matchday_points(r, fixtures, groups, next_n=1, **shared_kwargs),
                axis=1,
            )
            _best = _pos_pool.nlargest(1, "_pts").iloc[0]
            _in_name_str = str(_best["name"]).strip()
            _in_team_str = str(_best.get("team", "")).strip()
            _used_in_names.add(_in_name_str)
            _recs.append({
                "out_name":      _o.get("name", ""),
                "in_name":       _in_name_str,
                "out_team":      _o.get("team", ""),
                "in_team":       _in_team_str,
                "out_exp_pts":   _o.get("exp_pts", 0),
                "in_exp_pts":    float(_best["_pts"]),
                "reason":        _o.get("reason", ""),
                "urgency":       "",
                "in_next_game":  _next_game_date(_in_team_str),
            })
    if not _recs:
        st.info("No transfers suggested — your squad looks well-covered.")
    else:
        for _i, _rec in enumerate(_recs):
            _out_n = str(_rec.get("out_name", ""))
            _in_n  = str(_rec.get("in_name", ""))
            _out_team = str(_rec.get("out_team", ""))
            _in_team  = str(_rec.get("in_team", ""))
            _out_pts     = float(_rec.get("out_exp_pts", 0))
            _in_pts      = float(_rec.get("in_exp_pts", 0))
            _gain        = _in_pts - _out_pts
            _gain_str    = f"+{_gain:.1f}" if _gain >= 0 else f"{_gain:.1f}"
            _reason      = str(_rec.get("reason", ""))
            _urgency     = str(_rec.get("urgency", ""))
            _in_next     = str(_rec.get("in_next_game", ""))

            _c1, _c2, _c3 = st.columns([3, 3, 2])
            with _c1:
                st.markdown(
                    f"<div style='padding:10px;background:rgba(127,29,29,0.3);"
                    f"border-left:3px solid #ef4444;border-radius:6px'>"
                    f"<div style='font-size:10px;color:#f87171;font-weight:700'>OUT</div>"
                    f"<div style='font-size:14px;font-weight:700;color:#f1f5f9'>"
                    f"{display_name(_out_n)}</div>"
                    f"<div style='font-size:11px;color:#94a3b8'>{_out_team} · ~{_out_pts:.1f} pts</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            with _c2:
                _date_line = (
                    f"<div style='font-size:10px;color:#fbbf24;margin-top:4px'>next: {_in_next}</div>"
                    if _in_next else ""
                )
                st.markdown(
                    f"<div style='padding:10px;background:rgba(20,83,45,0.3);"
                    f"border-left:3px solid #22c55e;border-radius:6px'>"
                    f"<div style='font-size:10px;color:#4ade80;font-weight:700'>IN</div>"
                    f"<div style='font-size:14px;font-weight:700;color:#f1f5f9'>"
                    f"{display_name(_in_n)}</div>"
                    f"<div style='font-size:11px;color:#94a3b8'>{_in_team} · ~{_in_pts:.1f} pts"
                    f" <span style='color:#fbbf24'>({_gain_str})</span></div>"
                    f"{_date_line}"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            with _c3:
                st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
                if st.button(f"Make transfer", key=f"rec_xfer_{_i}", type="primary"):
                    _fresh = load_csv("players.csv")
                    _fresh.loc[
                        _fresh["name"].astype(str).str.strip() == _out_n.strip(), "in_squad"
                    ] = "False"
                    _fresh.loc[
                        _fresh["name"].astype(str).str.strip() == _in_n.strip(), "in_squad"
                    ] = "True"
                    save_csv("players.csv", _fresh)
                    record_transfer(_out_n.strip(), _in_n.strip())
                    for _k in list(st.session_state.keys()):
                        if not _k.startswith("chat_"):
                            del st.session_state[_k]
                    st.rerun()
                if _urgency:
                    st.caption(_urgency)
            if _reason:
                st.caption(_reason)
            if _i < len(_recs) - 1:
                st.markdown("<hr style='margin:6px 0;opacity:0.2'>", unsafe_allow_html=True)

st.divider()

# ── Fixture breakdown (collapsed) ─────────────────────────────────────────────
with st.expander("Squad Fixture Breakdown (next 4 games)"):
    ft = squad_fixture_table(squad, fixtures, groups, next_n=4)
    if not ft.empty:
        def color_difficulty(val):
            colors = {"Easy": "#166534", "Medium": "#854d0e", "Hard": "#7f1d1d"}
            bg = colors.get(val, "")
            return f"background-color: {bg}; color: white; border-radius: 4px; padding: 2px 6px;" if bg else ""
        st.dataframe(ft.style.map(color_difficulty, subset=["Fixtures"]),
                     use_container_width=True, hide_index=True)
    else:
        st.info("Fill in Fixtures and Groups data to see the breakdown.")

st.divider()

# ── Quick Transfer ─────────────────────────────────────────────────────────────
st.markdown("### Custom Transfer")
st.caption("Pick any player to transfer out and choose their replacement.")

if _transfers_left <= 0:
    st.warning("No transfers remaining.")
elif squad.empty:
    st.info("Squad not loaded.")
else:
    # ── OUT selector ──────────────────────────────────────────────────────────
    squad_opts = [("", "— select player to transfer out —")] + [
        (str(r["name"]), f"{display_name(str(r['name']))}  ({str(r.get('position','?')).upper()}, {str(r.get('team',''))})")
        for _, r in squad.iterrows()
    ]
    out_name = st.selectbox(
        "Transfer out",
        options=[o[0] for o in squad_opts],
        format_func=lambda k: dict(squad_opts).get(k, k),
        key="qt_out",
    )

    if out_name:
        out_row = squad[squad["name"].astype(str).str.strip() == out_name.strip()].iloc[0]
        out_pos  = str(out_row.get("position", "")).upper()
        out_val  = parse_value(str(out_row.get("value", 0)))

        # ── Find available replacements ────────────────────────────────────
        squad_names = set(squad["name"].astype(str).str.strip())
        avail = players[
            (~players["name"].astype(str).str.strip().isin(squad_names)) &
            (players["position"].astype(str).str.upper() == out_pos)
        ].copy()

        if avail.empty:
            st.warning(f"No available {out_pos} replacements found.")
        else:
            budget_slack = BUDGET - budget_used + out_val
            avail_ok = avail[avail["value"].apply(parse_value) <= budget_slack].copy()
            if avail_ok.empty:
                avail_ok = avail.copy()

            with st.spinner("Ranking replacements…"):
                avail_ok["_pts"] = avail_ok.apply(
                    lambda r: expected_matchday_points(r, fixtures, groups, next_n=1, **shared_kwargs),
                    axis=1,
                )
            top10 = avail_ok.nlargest(10, "_pts").reset_index(drop=True)

            # Show ranked table
            tbl_rows = []
            for _, r in top10.iterrows():
                pt = str(r.get("penalty_taker", "")).lower()
                spr = str(r.get("set_piece_role", "")).lower()
                flags = []
                if pt in ("primary", "secondary"):
                    flags.append("Pen")
                if spr not in ("no", "none", ""):
                    flags.append("SP")
                tbl_rows.append({
                    "Player":     display_name(str(r["name"])),
                    "Team":       str(r.get("team", "")),
                    "Value":      f"{parse_value(str(r['value']))/1_000_000:.2f}M",
                    "Pts (next)": round(float(r["_pts"]), 1),
                    "Roles":      " ".join(flags) if flags else "—",
                })
            st.dataframe(pd.DataFrame(tbl_rows), use_container_width=True, hide_index=True)
            st.caption("Pts (next): expected futispörssi points for the next single fixture.")

            # ── IN selector ───────────────────────────────────────────────
            in_opts = [("", "— select replacement —")] + [
                (
                    str(r["name"]),
                    f"{display_name(str(r['name']))}  —  {str(r.get('team',''))}  —  {parse_value(str(r['value']))/1_000_000:.2f}M  —  {round(float(r['_pts']),1)} pts",
                )
                for _, r in top10.iterrows()
            ]
            in_name = st.selectbox(
                "Transfer in",
                options=[o[0] for o in in_opts],
                format_func=lambda k: dict(in_opts).get(k, k),
                key="qt_in",
            )

            if in_name:
                c_ok, c_cancel, _ = st.columns([1, 1, 5])
                with c_ok:
                    if st.button("Confirm", type="primary", key="qt_confirm"):
                        fresh = load_csv("players.csv")
                        fresh.loc[
                            fresh["name"].astype(str).str.strip() == out_name.strip(), "in_squad"
                        ] = "False"
                        fresh.loc[
                            fresh["name"].astype(str).str.strip() == in_name.strip(), "in_squad"
                        ] = "True"
                        save_csv("players.csv", fresh)
                        record_transfer(out_name.strip(), in_name.strip())
                        for _k in list(st.session_state.keys()):
                            if not _k.startswith("chat_"):
                                del st.session_state[_k]
                        st.rerun()
                with c_cancel:
                    if st.button("Cancel", key="qt_cancel"):
                        st.rerun()

# ── Sidebar: Transfer Assistant chat ──────────────────────────────────────────
with st.sidebar:
    st.markdown("### Transfer Assistant")

    # Resolve API key: env file → os.environ → user input (session only, never saved)
    _chat_key = st.session_state.get("chat_api_key", "")
    if not _chat_key:
        _chat_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not _chat_key:
        _env_path = Path(__file__).parent.parent / "api_key.env"
        if _env_path.exists():
            for _line in _env_path.read_text().splitlines():
                if _line.startswith("ANTHROPIC_API_KEY="):
                    _chat_key = _line.split("=", 1)[1].strip()
                    break

    if not _chat_key:
        _key_inp = st.text_input(
            "Anthropic API key",
            type="password",
            placeholder="sk-ant-...",
            key="chat_key_input",
        )
        st.caption("Held in memory only — never saved to disk.")
        if _key_inp:
            st.session_state["chat_api_key"] = _key_inp
            st.rerun()
    else:
        # Build squad context for system prompt
        _squad_lines = "\n".join(
            f"- {display_name(str(r['name']))} "
            f"({str(r.get('position','')).upper()}, {r.get('team','')}, "
            f"{parse_value(str(r.get('value','0')))/1_000_000:.2f}M€)"
            for _, r in squad.iterrows()
        )
        _next_fx_lines = "\n".join(
            f"- {t}: {g}" for t, g in _team_next_game.items()
        )
        _system_prompt = f"""You are a Futispörssi fantasy football assistant for World Cup 2026.

Current squad:
{_squad_lines}

Budget remaining: {budget_left/1_000_000:.2f}M €
Transfers remaining: {_transfers_left} / {MAX_TRANSFERS}
Captain: {display_name(captain)} (~{cap_pts:.1f} pts as captain)

Upcoming fixtures for squad teams:
{_next_fx_lines}

Be concise and practical. Help the user decide transfers, captaincy, and strategy.
When suggesting transfers, always respect position rules (GK for GK, etc.) and budget."""

        if "chat_history" not in st.session_state:
            st.session_state["chat_history"] = []

        # Chat history display
        _chat_container = st.container(height=420)
        with _chat_container:
            if not st.session_state["chat_history"]:
                st.caption("Ask about your squad, upcoming fixtures, or transfer options.")
            for _msg in st.session_state["chat_history"]:
                with st.chat_message(_msg["role"]):
                    st.markdown(_msg["content"])

        # Input form
        with st.form("chat_form", clear_on_submit=True):
            _user_msg = st.text_area(
                "Message",
                placeholder="e.g. Who should I captain this weekend?",
                height=70,
                label_visibility="collapsed",
            )
            _col_send, _col_clear = st.columns([3, 1])
            _send = _col_send.form_submit_button("Send", use_container_width=True, type="primary")
            _clear = _col_clear.form_submit_button("Clear", use_container_width=True)

        if _clear:
            st.session_state["chat_history"] = []
            st.rerun()

        if _send and _user_msg.strip():
            st.session_state["chat_history"].append(
                {"role": "user", "content": _user_msg.strip()}
            )
            try:
                import anthropic as _ant
                _client = _ant.Anthropic(api_key=_chat_key)
                _history = st.session_state["chat_history"][-10:]
                _response = _client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=512,
                    system=_system_prompt,
                    messages=_history,
                )
                _reply = _response.content[0].text
                st.session_state["chat_history"].append(
                    {"role": "assistant", "content": _reply}
                )
            except Exception as _e:
                st.session_state["chat_history"].append(
                    {"role": "assistant", "content": f"Error: {_e}"}
                )
            st.rerun()

        # Key reset link
        if st.button("Change API key", key="chat_reset_key"):
            st.session_state.pop("chat_api_key", None)
            st.rerun()
