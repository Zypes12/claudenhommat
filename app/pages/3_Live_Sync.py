import sys
import datetime
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.data import load_csv, save_csv
from utils.football_api import (
    load_api_key, save_api_key,
    load_last_sync, save_last_sync,
    test_connection, fetch_wc_matches,
    build_results_df as _fdorg_build_results,
    merge_into_results as _fdorg_merge,
    APIError,
)
from utils.flashscore import sync_flashscore, ScrapeError

st.set_page_config(page_title="Live Sync", page_icon="🔗", layout="wide")
st.title("🔗 Live Data Sync")
st.caption(
    "Two sync sources — use either or both. "
    "**Flashscore** (no key needed) gives results, goalscorers, and confirmed lineups. "
    "**football-data.org** gives clean results via their official API."
)

tab_fs, tab_fdorg = st.tabs(["⚡ Flashscore  (results + lineups, no key)", "📡 football-data.org  (results only)"])


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — Flashscore
# ═══════════════════════════════════════════════════════════════════════════════
with tab_fs:
    st.markdown("### Flashscore Sync")
    st.caption(
        "No API key required. Scrapes the public Flashscore scoreboard and individual "
        "match pages. Results update instantly; **lineups are available once a match "
        "has kicked off** — Flashscore releases confirmed XIs about 1 hour before and "
        "locks them at kickoff."
    )

    fs_last = load_last_sync()
    if fs_last:
        try:
            ts = datetime.datetime.fromisoformat(fs_last)
            age_min = int((datetime.datetime.now() - ts).total_seconds() / 60)
            age_str = f"{age_min} min ago" if age_min < 60 else f"{age_min // 60}h ago"
            st.caption(f"Last synced: **{ts.strftime('%Y-%m-%d %H:%M')}** ({age_str})")
        except ValueError:
            pass

    fetch_lineups = st.checkbox(
        "Also fetch confirmed lineups",
        value=True,
        help=(
            "Makes one extra HTTP request per finished match to get the starting XI. "
            "Disable if you only want scores quickly."
        ),
    )

    if st.button("⚡ Sync from Flashscore", type="primary"):
        existing_results = load_csv("results.csv")
        existing_lineups = load_csv("lineups.csv")

        with st.spinner("Fetching from Flashscore…"):
            try:
                out = sync_flashscore(
                    existing_results,
                    existing_lineups,
                    fetch_lineups=fetch_lineups,
                )
            except ScrapeError as e:
                st.error(f"**Sync failed:** {e}")
                st.stop()

        if out["result_changes"]:
            st.success(f"**{len(out['result_changes'])} score(s) updated:**")
            for c in out["result_changes"]:
                st.markdown(f"- {c}")
        else:
            st.info("Scores: everything already up to date.")

        if out["lineup_changes"]:
            st.success(
                f"**{len(out['lineup_changes'])} team lineup(s) updated:** "
                + ", ".join(out["lineup_changes"])
            )
        elif fetch_lineups:
            if out["matches_fetched"] == 0:
                st.info("Lineups: no finished matches yet — sync again after kickoff.")
            else:
                st.info(
                    "Lineups: detail feeds returned no lineup data yet "
                    "(may need a few minutes after kickoff)."
                )

        if out["errors"]:
            with st.expander("⚠️ Errors during detail fetch"):
                for e in out["errors"]:
                    st.warning(e)

        save_csv("results.csv", out["results"])
        save_csv("lineups.csv", out["lineups"])
        now_str = datetime.datetime.now().isoformat(timespec="seconds")
        save_last_sync(now_str)
        st.caption(
            f"Saved at {now_str}  ·  "
            f"{out['matches_fetched']} finished matches scanned  ·  "
            f"{out['details_parsed']} detail feeds parsed"
        )

    # Debug expander — shows raw feed files saved during sync
    debug_dir = Path(__file__).parent.parent.parent / "Data" / "debug"
    debug_files = list(debug_dir.glob("fs_detail_*.txt")) if debug_dir.exists() else []
    if debug_files:
        with st.expander(f"🔍 Raw feed files ({len(debug_files)}) — lineup field-code inspection"):
            st.caption(
                "These files contain the raw Flashscore detail feed for completed matches. "
                "If lineups aren't parsing correctly, share one so the field codes can be "
                "identified and the parser updated."
            )
            sel = st.selectbox("File", [f.name for f in sorted(debug_files)])
            if sel:
                raw = (debug_dir / sel).read_text(encoding="utf-8")
                st.text_area("Raw feed", raw[:3000], height=200)

    st.divider()

    # Current results preview
    st.markdown("### Current Results")
    results = load_csv("results.csv")
    played = results[
        results["home_score"].astype(str).str.strip().notna()
        & (results["home_score"].astype(str).str.strip() != "")
        & (results["home_score"].astype(str).str.strip() != "nan")
    ].copy()

    if played.empty:
        st.info("No results recorded yet.")
    else:
        disp = played[["date", "home_team", "home_score", "away_score", "away_team", "goalscorers"]].copy()
        disp.columns = ["Date", "Home", "H", "A", "Away", "Goalscorers"]
        st.dataframe(disp, use_container_width=True, hide_index=True)
        st.caption(f"**{len(played)} played** · {len(results) - len(played)} remaining")

    # Model impact
    if not played.empty:
        st.markdown("### Model Impact")
        st.caption("How actual tournament data blends into the expected-points model.")
        from logic.recommendations import compute_actual_stats
        actual = compute_actual_stats(results)
        team_games = actual.get("team_games", {})
        if team_games:
            rows = []
            for t, g in sorted(team_games.items(), key=lambda x: -x[1]):
                blend = round(g / (5 + g) * 100)
                rows.append({
                    "Team":          t,
                    "Games":         g,
                    "Goals/game":    round(actual["team_attack"].get(t, 0), 2),
                    "Conceded/game": round(actual["team_defense"].get(t, 0), 2),
                    "Model blend":   f"{blend}% actual",
                })
            st.dataframe(rows, use_container_width=True, hide_index=True)
            pg = actual.get("player_goals", {})
            if pg:
                st.markdown("**Goalscorers detected:**")
                top = sorted(pg.items(), key=lambda x: -x[1])[:15]
                st.dataframe(
                    [{"Player": k, "Goals": v} for k, v in top],
                    use_container_width=True, hide_index=True,
                )


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — football-data.org
# ═══════════════════════════════════════════════════════════════════════════════
with tab_fdorg:
    st.markdown("### football-data.org Sync")

    stored_key = load_api_key()
    with st.expander(
        "Configure API key" if not stored_key else "✅  API key configured  (click to change)",
        expanded=not stored_key,
    ):
        st.markdown(
            "Registration takes about 2 minutes:\n\n"
            "1. Go to **https://www.football-data.org/client/register**\n"
            "2. Enter your email and a password\n"
            "3. Check your inbox — your API key is in the confirmation email\n"
            "4. Paste it below and click **Save**"
        )
        key_input = st.text_input(
            "Paste your API key here",
            value=stored_key,
            type="password",
            placeholder="e.g. a1b2c3d4e5f6…",
        )
        cs, ct = st.columns([1, 2])
        with cs:
            if st.button("💾 Save key"):
                save_api_key(key_input)
                st.success("Saved.")
                st.rerun()
        with ct:
            if st.button("🔌 Test connection"):
                with st.spinner("Connecting…"):
                    ok, msg = test_connection(key_input or stored_key)
                (st.success if ok else st.error)(msg)

    api_key = load_api_key()
    st.divider()

    if not api_key:
        st.warning("⚠️  Set your API key above before syncing.")
    else:
        last_sync = load_last_sync()
        if last_sync:
            try:
                ts = datetime.datetime.fromisoformat(last_sync)
                age_min = int((datetime.datetime.now() - ts).total_seconds() / 60)
                st.caption(f"Last synced: **{ts.strftime('%Y-%m-%d %H:%M')}** "
                           f"({age_min} min ago)")
            except ValueError:
                pass

        if st.button("🔄 Sync results from football-data.org", type="primary"):
            with st.spinner("Fetching…"):
                try:
                    matches  = fetch_wc_matches(api_key)
                    fetched  = _fdorg_build_results(matches)
                    existing = load_csv("results.csv")
                    merged, changes = _fdorg_merge(existing, fetched)
                except APIError as e:
                    st.error(f"**Sync failed:** {e}")
                    st.stop()

            if not changes:
                st.info("Everything already up to date.")
            else:
                st.success(f"**{len(changes)} result(s) updated:**")
                for c in changes:
                    st.markdown(f"- {c}")

            save_csv("results.csv", merged)
            save_last_sync(datetime.datetime.now().isoformat(timespec="seconds"))
