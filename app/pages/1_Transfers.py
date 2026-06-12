import sys
import datetime
from pathlib import Path

import streamlit as st
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.data import load_csv
from utils.team_form import load_team_form, get_form_stats
from logic.recommendations import (
    recommend_best_squad, load_user_squad, squad_coverage_gaps,
    recommend_transfers, get_transfer_schedule,
    fixture_difficulty, difficulty_label, display_name,
    compute_actual_stats, compute_recent_form, compute_group_standings, compute_advance_probability,
    _enrich_with_team, BUDGET, MAX_TRANSFERS, POS_COLORS,
)

st.set_page_config(page_title="Transfers", layout="wide")

st.title("Transfer Planner")

# ── Load data first (needed for free-selection banner) ─────────────────────────
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
        f"**Initial squad selection is FREE** — adjust your starting 11 at no cost "
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
              delta="low" if remaining < 8 else f"{remaining} left",
              delta_color="inverse" if remaining < 8 else "off")
with c3:
    st.markdown("<br>", unsafe_allow_html=True)
    st.progress(used / MAX_TRANSFERS, text=f"{used} / {MAX_TRANSFERS} used")

st.divider()

# ── Load squad: user's actual squad first, fall back to algorithm ─────────────
with st.spinner("Loading squad…"):
    shared_kwargs = dict(form=form, actual_stats=actual_stats, form_stats=form_stats, recent_form=recent_form)
    result = load_user_squad(players, lineups, fixtures, groups, **shared_kwargs)
    using_user_squad = result is not None
    if not using_user_squad:
        result = recommend_best_squad(players, fixtures, groups, lineups, **shared_kwargs)

if result is None:
    st.warning("Not enough data. Check the **Data** page.")
    st.stop()

squad    = result["squad"]
enriched = result.get("enriched_players", _enrich_with_team(players, lineups))
today    = datetime.date.today().isoformat()

# ── My Squad + Coverage Map ────────────────────────────────────────────────────
squad_label = "My Squad" if using_user_squad else "Recommended Squad"
st.markdown(f"## {squad_label}")

s_cols = st.columns(len(squad))
pos_order = {"GK": 0, "DEF": 1, "MID": 2, "FWD": 3}
for col, (_, p) in zip(s_cols, squad.sort_values("position", key=lambda s: s.map(pos_order)).iterrows()):
    pos   = str(p.get("position", "")).upper()
    color = POS_COLORS.get(pos, "#7c3aed")
    col.markdown(
        f"<div style='text-align:center;border-top:3px solid {color};padding:6px 2px;'>"
        f"<div style='font-size:9px;color:{color};font-weight:700'>{pos}</div>"
        f"<div style='font-size:12px;font-weight:700'>{display_name(str(p.get('name','')))}</div>"
        f"<div style='font-size:10px;color:#64748b'>{p.get('team','')}</div>"
        f"<div style='font-size:11px;color:#38bdf8'>~{float(p.get('exp_pts',0)):.1f} pts</div>"
        f"</div>",
        unsafe_allow_html=True,
    )

st.markdown("### Coverage Map")
gaps, covered = squad_coverage_gaps(squad, fixtures)

if gaps:
    st.error(f"**{len(gaps)} gap day(s)** — no player in action: " + "  |  ".join(gaps))

# Draw a compact calendar grid
june_dates = sorted(
    d for d in fixtures["date"].astype(str).str.strip().unique()
    if d.startswith("2026-06")
)
covered_map = {date: teams for date, teams in covered}
cal_cols = st.columns(len(june_dates))
for col, date in zip(cal_cols, june_dates):
    day = date[8:]  # DD
    if date in covered_map:
        teams_str = ", ".join(covered_map[date])
        col.markdown(
            f"<div style='text-align:center;background:#14532d;border-radius:6px;"
            f"padding:4px 2px;font-size:10px'>"
            f"<b style='color:#4ade80'>Jun {day}</b><br>"
            f"<span style='color:#86efac;font-size:9px'>{teams_str}</span></div>",
            unsafe_allow_html=True,
        )
    else:
        col.markdown(
            f"<div style='text-align:center;background:#7f1d1d;border-radius:6px;"
            f"padding:4px 2px;font-size:10px'>"
            f"<b style='color:#f87171'>Jun {day}</b><br>"
            f"<span style='color:#fca5a5;font-size:9px'>GAP</span></div>",
            unsafe_allow_html=True,
        )

st.divider()
today    = datetime.date.today().isoformat()

# ── Transfer strategy banner ───────────────────────────────────────────────────
with st.expander("Transfer rules & strategy", expanded=False):
    st.markdown("""
**Timing:** Transfers can be made any time before the **first game of the day in Eastern Time (EDT/EST)**.
WC 2026 is in North America — a 9 pm EDT kickoff on June 14 means your deadline is still June 14, not June 15.

**Key rules:**
- Before the tournament starts → **free** squad selection, no transfers used
- Once tournament kicks off → each change costs 1 of your **35 transfers**
- **Never transfer out a player who plays that day** — you forfeit the points
- **Priority 1:** Fill coverage gaps — days when none of your players has a game
- **Priority 2:** Upgrade when a much-better player is available who plays today
- **Captain tip:** Rotate captaincy to whoever plays that day with the best matchup

**35-transfer strategy:**
- Group stage (June 11–27): use ≤ 8 transfers — only for gaps or clear upgrades
- Round of 32 (June 28 – July 3): use 4–5 transfers as knockout brackets become clear
- Round of 16 → Final: save 12+ transfers for player swaps as teams get eliminated
""")

# ── Transfer schedule ──────────────────────────────────────────────────────────
st.markdown("## Transfer Windows by Round")
st.caption(
    "Each day's transfer window closes before the **first kickoff of that day (Eastern Time)**. "
    "Players playing today cannot be transferred out — their points window has opened."
)

schedule = get_transfer_schedule(squad, enriched, fixtures, groups, used, today, form=form, actual_stats=actual_stats, form_stats=form_stats, recent_form=recent_form, advance_probs=advance_probs)

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
            f"[{urgency}]  **{label}**  —  {date_range}  "
            f"·  recommended transfers: **{n_swaps}**",
            expanded=(days_start <= 1),
        ):
            uncovered = window.get("uncovered_days", [])
            if uncovered:
                st.error(
                    "**Coverage gaps** — no squad player plays on: "
                    + "  |  ".join(uncovered)
                    + "  — priority transfer targets"
                )

            # Day-specific transfer suggestions
            st.markdown("**Recommended transfers:**")
            if swaps:
                for s in swaps:
                    d_away_s = s["days_until"]
                    if d_away_s == 0:
                        when = "**TODAY** — before first game (Eastern Time)"
                    elif d_away_s == 1:
                        when = "**tomorrow** before first game (Eastern Time)"
                    else:
                        when = f"in **{d_away_s} days** (before first game, Eastern Time)"
                    is_gap = s["transfer_date"] in uncovered

                    is_free = bool(tournament_start and s["transfer_date"] <= tournament_start)
                    free_badge = "  — FREE (before tournament starts)" if is_free else ""
                    st.markdown(
                        f"Transfer {when} — `{s['transfer_date']}`"
                        + ("  — **covers gap day**" if is_gap else "")
                        + free_badge,
                        unsafe_allow_html=False,
                    )
                    c1, c2 = st.columns(2)
                    rebuy_note = "  ·  *Candidate to rebuy later*" if s.get("can_buy_back") else ""
                    c1.markdown(
                        f"**OUT:** {s['out']}  \n"
                        f"*{s['out_team']} — no game this day*{rebuy_note}"
                    )
                    c2.markdown(
                        f"**IN:** {s['in']}  \n"
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
                gap_flag = "  — NO COVERAGE" if not has_squad else ""

                if d_away < 0:
                    day_label = f"~~{d_str}~~ (played)"
                elif d_away == 0:
                    day_label = f"**{d_str} — TODAY**{gap_flag}"
                else:
                    day_label = f"{d_str}  ({d_away}d){gap_flag}"

                game_lines = []
                for g in games:
                    h_mark = "S" if g["squad_home"] else "·"
                    a_mark = "S" if g["squad_away"] else "·"
                    game_lines.append(f"{h_mark}  **{g['home']}** vs **{g['away']}**  {a_mark}")

                with st.container():
                    st.markdown(f"*{day_label}*")
                    for line in game_lines:
                        st.markdown(f"&nbsp;&nbsp;&nbsp;{line}", unsafe_allow_html=True)
            st.caption("S = squad player in this team  ·  NO COVERAGE = gap day")

st.divider()

# ── Full transfer schedule (flat table) ───────────────────────────────────────
st.markdown("## Full Transfer Schedule")
st.caption(
    "Every recommended transfer across all rounds in one table. "
    "Gap = day when no squad player plays — top priority. "
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
        is_free_swap = bool(tournament_start and s["transfer_date"] <= tournament_start)
        in_adv = s.get("in_advance_prob", 1.0)
        adv_str = f"{in_adv:.0%}" if in_adv < 0.80 else ""
        all_swaps.append({
            "Date":      s["transfer_date"],
            "When":      when_str,
            "Free":      "FREE" if is_free_swap else "",
            "Round":     window["round_label"],
            "Pos":       s["position"],
            "OUT":       s["out"],
            "OUT Team":  s["out_team"],
            "IN":        s["in"],
            "IN Team":   s["in_team"],
            "Day pts":   s["day_pts"],
            "Net":       f"{s['pts_gain']:+.1f}",
            "Gap":       "GAP" if s.get("is_gap_day") else "",
            "Rebuy?":    "rebuy" if s.get("can_buy_back") else "",
            "Adv%":      adv_str,
        })

if all_swaps:
    sched_df = pd.DataFrame(all_swaps)
    st.dataframe(sched_df, use_container_width=True, hide_index=True)
    total_shown = len(all_swaps)
    gap_count   = sum(1 for s in all_swaps if s["Gap"] == "GAP")
    st.caption(
        f"**{total_shown} transfers** recommended across {len(schedule)} rounds  ·  "
        f"**{gap_count}** cover a gap day  ·  "
        f"**{remaining}** transfer slots remaining"
    )
else:
    st.info("No transfer recommendations found.")

st.divider()

# ── Transfer Explorer ──────────────────────────────────────────────────────────
st.markdown("## Transfer Explorer")
st.caption("Find the best available replacements for any position.")

ca, cb = st.columns([1, 3])
pos_filter = ca.radio("Position", ["All", "GK", "DEF", "MID", "FWD"], horizontal=False)
n = cb.slider("Suggestions per side", 3, 10, 5)

pf = None if pos_filter == "All" else pos_filter
transfers = recommend_transfers(squad, enriched, fixtures, groups, n_suggestions=n, position_filter=pf, form=form, actual_stats=actual_stats, form_stats=form_stats, today_str=today, recent_form=recent_form)

col_out, col_in = st.columns(2)

def fmt_list(lst, show_next_game=False):
    rows = []
    for s in lst:
        row = {
            "Player": display_name(s["name"]),
            "Pos":    s["position"],
            "Team":   s.get("team", "—"),
            "Pts/g":  s["exp_pts"],
        }
        if show_next_game:
            nd = s.get("next_date", "")
            no = s.get("next_opp", "")
            row["Next game"] = f"{nd}  vs  {no}" if nd and no else "—"
        row["Notes"] = s.get("reason", "")
        rows.append(row)
    return pd.DataFrame(rows) if rows else pd.DataFrame()

with col_out:
    st.markdown("#### Weakest in current squad")
    st.caption("Ranked by 3-game average — lower = first to consider selling.")
    df = fmt_list(transfers.get("out", []))
    if not df.empty:
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No candidates.")

with col_in:
    st.markdown("#### Best available")
    st.caption("Ranked by next-fixture expected pts — reflects who is worth buying RIGHT NOW.")
    df = fmt_list(transfers.get("in", []), show_next_game=True)
    if not df.empty:
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No candidates.")

st.divider()

# ── Group standings + advance probability ──────────────────────────────────────
st.markdown("## Group Standings & Advance Probability")
st.caption(
    "Estimated probability each team advances from the group stage. "
    "Scores update automatically as results are recorded. "
    "Top 2 per group qualify; best 8 third-place teams also advance."
)

if not groups.empty and _standings:
    squad_teams = set(squad["team"].astype(str).str.strip().tolist())
    adv_rows = []
    for _, grp_row in groups.iterrows():
        t = str(grp_row["team"]).strip()
        s = _standings.get(t, {"pts": 0, "gd": 0, "gf": 0, "ga": 0, "played": 0})
        adv = advance_probs.get(t, 0.5)
        adv_rows.append({
            "Group":    str(grp_row.get("group", "")),
            "Team":     display_name(t),
            "P":        s.get("played", 0),
            "Pts":      s.get("pts", 0),
            "GD":       s.get("gd", 0),
            "GF":       s.get("gf", 0),
            "Adv%":     f"{adv:.0%}",
            "_adv":     adv,
            "_in_squad": t in squad_teams,
        })

    adv_df = (
        pd.DataFrame(adv_rows)
        .sort_values(["Group", "Pts", "GD", "GF"], ascending=[True, False, False, False])
        .reset_index(drop=True)
    )

    def _adv_style(row):
        adv = row["_adv"]
        in_sq = row["_in_squad"]
        bg = ""
        if adv >= 0.90:   bg = "background-color:#14532d"
        elif adv >= 0.60: bg = "background-color:#1c4f1c"
        elif adv >= 0.35: bg = "background-color:#78350f"
        else:              bg = "background-color:#7f1d1d"
        border = ";outline: 2px solid gold" if in_sq else ""
        return [f"{bg}{border};color:white"] * len(row)

    display_cols = ["Group", "Team", "P", "Pts", "GD", "GF", "Adv%"]
    styled_adv = adv_df[display_cols + ["_adv", "_in_squad"]].style.apply(_adv_style, axis=1)
    st.dataframe(
        styled_adv[display_cols],
        use_container_width=True,
        hide_index=True,
    )
    st.caption("Dark green = high advance probability  ·  Red = likely eliminated  ·  Gold outline = your squad player")
else:
    st.info("Load Groups and Results data to see standings.")

st.divider()

# ── Fixture calendar ───────────────────────────────────────────────────────────
st.markdown("## Squad Fixture Calendar")
st.caption("Easy / Medium / Hard — color-coded by opponent FIFA ranking")

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
            fx_cells[col_lbl] = f"{lbl}: {opp}"

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

        _fx_cols = [c for c in cal_df.columns if c.startswith("MD") or c == "KO"]

        def _style_fx(val):
            v = str(val)
            if v.startswith("Easy"):    return "color:#4ade80;font-weight:500"
            if v.startswith("Medium"):  return "color:#fbbf24;font-weight:500"
            if v.startswith("Hard"):    return "color:#f87171;font-weight:500"
            return ""

        styled_cal = cal_df.style.map(_style_fx, subset=_fx_cols) if _fx_cols else cal_df.style
        st.dataframe(styled_cal, use_container_width=True, hide_index=True)
else:
    st.info("Load Fixtures and Groups data first.")
