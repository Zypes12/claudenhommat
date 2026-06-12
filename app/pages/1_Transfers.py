import sys
import datetime
from pathlib import Path

import streamlit as st
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.data import load_csv, load_transfer_count
from utils.styles import inject_shared_css
from utils.team_form import load_team_form, get_form_stats
from logic.recommendations import (
    recommend_best_squad, load_user_squad, squad_coverage_gaps,
    recommend_transfers, get_transfer_schedule,
    fixture_difficulty, difficulty_label, display_name,
    compute_actual_stats, compute_recent_form, compute_group_standings, compute_advance_probability,
    _enrich_with_team, BUDGET, MAX_TRANSFERS, POS_COLORS,
)

st.set_page_config(page_title="Transfer Analysis", layout="wide")
inject_shared_css()

st.title("Transfer Analysis")

# ── Load data ─────────────────────────────────────────────────────────────────
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

_all_dates = sorted([
    d for d in fixtures["date"].astype(str).str.strip().tolist()
    if d and d not in ("nan", "")
])
tournament_start = _all_dates[0] if _all_dates else None

used = load_transfer_count()
remaining = MAX_TRANSFERS - used

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

# ── Coverage Calendar ─────────────────────────────────────────────────────────
st.markdown("## Coverage Calendar")

gaps, covered = squad_coverage_gaps(squad, fixtures)
covered_map = {date: teams for date, teams in covered}

_all_fix_dates = sorted(
    d for d in fixtures["date"].astype(str).str.strip().unique()
    if d and d not in ("nan", "") and d >= today
)

if _all_fix_dates:
    _cells = []
    for _date in _all_fix_dates:
        try:
            _d_obj = datetime.date.fromisoformat(_date)
            _day_lbl = _d_obj.strftime("%-d %b")
        except Exception:
            _day_lbl = _date[5:]

        if _date in covered_map:
            _teams = " · ".join(covered_map[_date])
            _cells.append(
                f"<div style='flex:0 0 auto;text-align:center;background:rgba(20,83,45,0.75);"
                f"border:1px solid #22c55e;border-radius:6px;padding:6px 8px;min-width:70px;max-width:110px;'>"
                f"<div style='font-size:11px;font-weight:700;color:#4ade80'>{_day_lbl}</div>"
                f"<div style='font-size:9px;color:#86efac;margin-top:2px;line-height:1.3'>{_teams}</div>"
                f"</div>"
            )
        else:
            _cells.append(
                f"<div style='flex:0 0 auto;text-align:center;background:rgba(127,29,29,0.75);"
                f"border:1px solid #ef4444;border-radius:6px;padding:6px 8px;min-width:70px;max-width:110px;'>"
                f"<div style='font-size:11px;font-weight:700;color:#f87171'>{_day_lbl}</div>"
                f"<div style='font-size:9px;color:#fca5a5;margin-top:2px'>GAP</div>"
                f"</div>"
            )

    st.markdown(
        "<div style='display:flex;flex-wrap:wrap;gap:6px;padding:4px 0'>"
        + "".join(_cells)
        + "</div>",
        unsafe_allow_html=True,
    )

    if gaps:
        st.caption(f"Red = gap day — no squad player plays · {len(gaps)} gap(s) found")
    else:
        st.caption("All days covered — no gaps in squad coverage")

st.divider()

# ── Full transfer schedule (flat table) ───────────────────────────────────────
with st.spinner("Computing transfer schedule…"):
    schedule = get_transfer_schedule(squad, enriched, fixtures, groups, used, today, form=form, actual_stats=actual_stats, form_stats=form_stats, recent_form=recent_form, advance_probs=advance_probs)

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
        is_ko = s.get("is_ko_round", False)
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
            "Surv%":     adv_str,
            "KO?":       "KO" if is_ko else "",
            "ST?":       "SHORT-TERM" if s.get("is_short_term") else "",
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

    display_cols = ["Group", "Team", "P", "Pts", "GD", "GF", "Adv%"]
    _adv_arr  = adv_df["_adv"].values
    _insq_arr = adv_df["_in_squad"].values
    display_df = adv_df[display_cols].reset_index(drop=True)

    def _adv_style(row):
        i = row.name
        adv   = _adv_arr[i]
        in_sq = _insq_arr[i]
        if adv >= 0.90:   bg = "background-color:#14532d"
        elif adv >= 0.60: bg = "background-color:#1c4f1c"
        elif adv >= 0.35: bg = "background-color:#78350f"
        else:              bg = "background-color:#7f1d1d"
        border = ";outline: 2px solid gold" if in_sq else ""
        return [f"{bg}{border};color:white"] * len(row)

    st.dataframe(
        display_df.style.apply(_adv_style, axis=1),
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
