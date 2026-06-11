import sys
import datetime
from pathlib import Path

import streamlit as st
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.data import load_csv
from logic.recommendations import (
    recommend_best_squad, recommend_transfers, get_transfer_schedule,
    fixture_difficulty, difficulty_label, display_name,
    _enrich_with_team, BUDGET, MAX_TRANSFERS, POS_COLORS,
)

st.set_page_config(page_title="Transfers", page_icon="🔄", layout="wide")

st.title("🔄 Transfer Planner")

# ── Load data first (needed for free-selection banner) ─────────────────────────
players  = load_csv("players.csv")
fixtures = load_csv("fixtures.csv")
groups   = load_csv("groups.csv")
lineups  = load_csv("lineups.csv")
form     = load_csv("form.csv")

# Determine whether we are still in the free initial selection window
# (before the very first game of the tournament — no transfer slot is used).
_all_dates = sorted([
    d for d in fixtures["date"].astype(str).str.strip().tolist()
    if d and d not in ("nan", "")
])
tournament_start = _all_dates[0] if _all_dates else None
today_date = datetime.date.today()
in_free_period = bool(
    tournament_start and today_date.isoformat() <= tournament_start
)

if in_free_period:
    st.success(
        f"⚡ **Initial squad selection is FREE** — adjust your starting 11 at no cost "
        f"before the first game on **{tournament_start}**. "
        "Your 35-transfer budget only starts counting once the tournament begins."
    )
else:
    st.caption(
        f"**{MAX_TRANSFERS} total transfers** for the whole tournament. "
        "Transfers are free before the first game — the 35-slot budget starts after kickoff."
    )

# ── Transfer counter ───────────────────────────────────────────────────────────
c1, c2, c3 = st.columns([1, 1, 2])
with c1:
    used = st.number_input("Transfers used so far", 0, MAX_TRANSFERS, 0, 1,
                           help="Count only transfers made after the tournament started.")
remaining = MAX_TRANSFERS - used
with c2:
    st.metric("Remaining", remaining,
              delta="⚠️ low" if remaining < 8 else f"{remaining} left",
              delta_color="inverse" if remaining < 8 else "off")
with c3:
    st.markdown("<br>", unsafe_allow_html=True)
    st.progress(used / MAX_TRANSFERS, text=f"{used} / {MAX_TRANSFERS} used")

st.divider()

# ── Generate squad ────────────────────────────────────────────────────────────
with st.spinner("Calculating…"):
    result = recommend_best_squad(players, fixtures, groups, lineups, form=form)

if result is None:
    st.warning("Not enough data. Check the **Data** page.")
    st.stop()

squad    = result["squad"]
enriched = result.get("enriched_players", _enrich_with_team(players, lineups))
today    = datetime.date.today().isoformat()

# ── Transfer schedule ──────────────────────────────────────────────────────────
st.markdown("## 📆 Transfer Windows by Round")
st.caption(
    "Each matchday round lasts ~7 days. "
    "**Best time to transfer: before the first game of the round** "
    "so all your players are covered for the full week. "
    "Mid-round transfers are also valid but cost the same 1 transfer each."
)

schedule = get_transfer_schedule(squad, enriched, fixtures, groups, used, today, form=form)

if not schedule:
    st.info("No upcoming rounds found.")
else:
    for window in schedule:
        label      = window["round_label"]
        start      = window["round_start"]
        end        = window["round_end"]
        span       = window["round_span_days"]
        days_start = window["days_to_start"]
        urgency    = window["urgency"]
        n_swaps    = window["suggested_transfers"]
        swaps      = window["pre_round_swaps"]
        daily      = window["daily_games"]

        if days_start <= 0:
            date_range = f"{start} → {end}  ·  **In progress** ({span}-day round)"
        else:
            date_range = f"{start} → {end}  ·  starts in {days_start} day{'s' if days_start != 1 else ''}"

        with st.expander(
            f"{urgency}  **{label}**  —  {date_range}  "
            f"·  recommended transfers: **{n_swaps}**",
            expanded=(days_start <= 1),
        ):
            uncovered = window.get("uncovered_days", [])
            if uncovered:
                st.error(
                    "⚠️ **Coverage gaps** — no squad player plays on: "
                    + "  |  ".join(uncovered)
                    + "  ← priority transfer targets"
                )

            # Day-specific transfer suggestions
            st.markdown("**Recommended transfers:**")
            if swaps:
                for s in swaps:
                    d_away_s = s["days_until"]
                    if d_away_s == 0:
                        when = "**TODAY** before kickoff"
                    elif d_away_s == 1:
                        when = "**tomorrow**"
                    else:
                        when = f"in **{d_away_s} days**"
                    is_gap = s["transfer_date"] in uncovered

                    st.markdown(
                        f"📅 Transfer {when} — `{s['transfer_date']}`"
                        + ("  🚨 *covers gap day*" if is_gap else ""),
                        unsafe_allow_html=False,
                    )
                    c1, c2 = st.columns(2)
                    c1.markdown(
                        f"↩️ **OUT:** {s['out']}  \n"
                        f"*{s['out_team']} — no game this day*"
                    )
                    c2.markdown(
                        f"✅ **IN:** {s['in']}  \n"
                        f"*{s['in_team']} — plays {s['transfer_date']}*  ·  "
                        f"**~{s['day_pts']} pts**"
                    )
                    st.caption(s["reason"])
                    st.markdown("---")
            else:
                st.success("No clear gains — hold your transfers for this round.")

            # Day-by-day game schedule
            st.markdown("**Game-day schedule:**")
            for day_info in daily:
                d_str   = day_info["date"]
                d_away  = day_info["days_away"]
                games   = day_info["games"]

                has_squad = any(g["squad_home"] or g["squad_away"] for g in games)
                gap_flag = "  🔴 *no squad player!*" if not has_squad else ""

                if d_away < 0:
                    day_label = f"~~{d_str}~~ (played)"
                elif d_away == 0:
                    day_label = f"**{d_str} — TODAY**{gap_flag}"
                else:
                    day_label = f"{d_str}  ({d_away}d){gap_flag}"

                game_lines = []
                for g in games:
                    h_icon = "⚽" if g["squad_home"] else "·"
                    a_icon = "⚽" if g["squad_away"] else "·"
                    game_lines.append(f"{h_icon} **{g['home']}** vs **{g['away']}** {a_icon}")

                with st.container():
                    st.markdown(f"*{day_label}*")
                    for line in game_lines:
                        st.markdown(f"&nbsp;&nbsp;&nbsp;{line}", unsafe_allow_html=True)
            st.caption("⚽ = your squad has a player from this team  ·  🔴 = no coverage")

st.divider()

# ── Full transfer schedule (flat table) ───────────────────────────────────────
st.markdown("## 📋 Full Transfer Schedule")
st.caption(
    "Every recommended transfer across all rounds in one table. "
    "Gap 🚨 = day when no squad player plays — top priority. "
    "Net = in-player day pts minus out-player average pts."
)

all_swaps = []
for window in schedule:
    for s in window["pre_round_swaps"]:
        d_away = s["days_until"]
        if d_away < 0:
            when_str = "Played"
        elif d_away == 0:
            when_str = "Today"
        elif d_away == 1:
            when_str = "Tomorrow"
        else:
            when_str = f"In {d_away}d"
        all_swaps.append({
            "Date":     s["transfer_date"],
            "When":     when_str,
            "Round":    window["round_label"],
            "Pos":      s["position"],
            "OUT":      s["out"],
            "OUT Team": s["out_team"],
            "IN":       s["in"],
            "IN Team":  s["in_team"],
            "Day pts":  s["day_pts"],
            "Net":      f"{s['pts_gain']:+.1f}",
            "Gap":      "🚨" if s.get("is_gap_day") else "",
        })

if all_swaps:
    sched_df = pd.DataFrame(all_swaps)
    st.dataframe(sched_df, use_container_width=True, hide_index=True)
    total_shown = len(all_swaps)
    gap_count   = sum(1 for s in all_swaps if s["Gap"] == "🚨")
    st.caption(
        f"**{total_shown} transfers** recommended across {len(schedule)} rounds  ·  "
        f"**{gap_count}** cover a gap day  ·  "
        f"**{remaining}** transfer slots remaining"
    )
else:
    st.info("No transfer recommendations found.")

st.divider()

# ── Transfer Explorer ──────────────────────────────────────────────────────────
st.markdown("## 🔍 Transfer Explorer")
st.caption("Find the best available replacements for any position.")

ca, cb = st.columns([1, 3])
pos_filter = ca.radio("Position", ["All", "GK", "DEF", "MID", "FWD"], horizontal=False)
n = cb.slider("Suggestions per side", 3, 10, 5)

pf = None if pos_filter == "All" else pos_filter
transfers = recommend_transfers(squad, enriched, fixtures, groups, n_suggestions=n, position_filter=pf, form=form)

col_out, col_in = st.columns(2)

def fmt_list(lst):
    rows = []
    for s in lst:
        rows.append({
            "Player": display_name(s["name"]),
            "Pos":    s["position"],
            "Team":   s.get("team", "—"),
            "Pts/g":  s["exp_pts"],
            "Notes":  s.get("reason", ""),
        })
    return pd.DataFrame(rows) if rows else pd.DataFrame()

with col_out:
    st.markdown("#### ↩️ Weakest in current squad")
    df = fmt_list(transfers.get("out", []))
    if not df.empty:
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No candidates.")

with col_in:
    st.markdown("#### ✅ Best available")
    df = fmt_list(transfers.get("in", []))
    if not df.empty:
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No candidates.")

st.divider()

# ── Fixture calendar ───────────────────────────────────────────────────────────
st.markdown("## 📅 Squad Fixture Calendar")
st.caption("🟢 Easy  🟡 Medium  🔴 Hard  — based on opponent FIFA ranking")

if not fixtures.empty and not groups.empty:
    unplayed = fixtures
    if "home_score" in fixtures.columns:
        unplayed = fixtures[
            fixtures["home_score"].isna() | (fixtures["home_score"].astype(str).str.strip() == "")
        ]

    from logic.recommendations import get_team_ranking
    rows = []
    for _, p in squad.iterrows():
        team = str(p.get("team", "")).strip()
        pos  = str(p.get("position", "")).upper()
        pts  = round(float(p.get("exp_pts", 0)), 1)

        team_fx = unplayed[
            (unplayed["home_team"].astype(str).str.strip() == team) |
            (unplayed["away_team"].astype(str).str.strip() == team)
        ].head(4)

        fx_cells: dict = {}
        for _, fx in team_fx.iterrows():
            md_raw = str(fx.get("matchday", "")).strip()
            stage  = str(fx.get("stage", "")).strip()
            if md_raw and md_raw not in ("nan", ""):
                col_lbl = f"MD{int(float(md_raw))}"
            else:
                col_lbl = stage[:6] if stage else "KO"

            opp  = fx["away_team"] if str(fx["home_team"]).strip() == team else fx["home_team"]
            rank = get_team_ranking(str(opp).strip(), groups)
            lbl, _ = difficulty_label(rank)
            icon = "🟢" if lbl == "Easy" else ("🟡" if lbl == "Medium" else "🔴")
            fx_cells[col_lbl] = f"{icon} {opp}"

        row = {
            "Player": display_name(str(p.get("name", ""))),
            "Pos":    pos,
            "Team":   team or "—",
            "Pts/g":  pts,
        }
        row.update(fx_cells)
        rows.append(row)

    if rows:
        cal_df = pd.DataFrame(rows).sort_values("Pts/g", ascending=False).reset_index(drop=True)
        st.dataframe(cal_df, use_container_width=True, hide_index=True)
else:
    st.info("Load Fixtures and Groups data first.")
