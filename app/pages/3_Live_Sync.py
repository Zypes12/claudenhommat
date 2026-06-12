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
from utils.futisporssi import sync_prices, discover_player_ids, FPScrapeError
from utils.team_form import (
    fetch_all_teams_form, load_team_form, save_team_form,
    get_form_stats, FORM_COLUMNS,
)

st.set_page_config(page_title="Live Sync", page_icon="🔗", layout="wide")
st.title("🔗 Live Data Sync")
st.caption(
    "Four data sources — Flashscore for results + lineups, "
    "Futispörssi for live player prices, pre-tournament form, and football-data.org."
)

tab_fs, tab_prices, tab_form, tab_fdorg = st.tabs([
    "⚡ Flashscore  (results + lineups, no key)",
    "💰 Player Prices  (futisporssi.fi)",
    "📊 Pre-tournament Form  (last 5 matches per team)",
    "📡 football-data.org  (results only)",
])


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
        "Also fetch confirmed lineups, goalscorers & assists",
        value=True,
        help=(
            "Makes two extra HTTP requests per finished match to get the starting XI, "
            "goalscorers, and assists. Disable if you only want scores quickly."
        ),
    )

    if st.button("⚡ Sync from Flashscore", type="primary"):
        existing_results      = load_csv("results.csv")
        existing_lineups      = load_csv("lineups.csv")
        existing_player_stats = load_csv("player_stats.csv")

        with st.spinner("Fetching from Flashscore…"):
            try:
                out = sync_flashscore(
                    existing_results,
                    existing_lineups,
                    existing_player_stats=existing_player_stats,
                    fetch_details=fetch_lineups,
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

        save_csv("results.csv",      out["results"])
        save_csv("lineups.csv",      out["lineups"])
        save_csv("player_stats.csv", out["player_stats"])
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
        cols = ["date", "home_team", "home_score", "away_score", "away_team", "goalscorers"]
        if "assists" in played.columns:
            cols.append("assists")
        disp = played[cols].copy()
        disp.columns = ["Date", "Home", "H", "A", "Away", "Goalscorers"] + (["Assists"] if "assists" in played.columns else [])
        st.dataframe(disp, use_container_width=True, hide_index=True)
        st.caption(f"**{len(played)} played** · {len(results) - len(played)} remaining")

    # Player stats
    player_stats = load_csv("player_stats.csv")
    if not player_stats.empty:
        st.markdown("### Player Stats")
        top_scorers = (
            player_stats.groupby("player_name")
            .agg(team=("team", "first"), goals=("goals", "sum"), assists=("assists", "sum"))
            .reset_index()
            .sort_values(["goals", "assists"], ascending=False)
        )
        top_scorers = top_scorers[top_scorers["goals"].astype(float) > 0]
        if not top_scorers.empty:
            st.markdown("**Top scorers**")
            st.dataframe(top_scorers.head(20), use_container_width=True, hide_index=True)
        top_assists = (
            player_stats.groupby("player_name")
            .agg(team=("team", "first"), assists=("assists", "sum"), goals=("goals", "sum"))
            .reset_index()
            .sort_values(["assists", "goals"], ascending=False)
        )
        top_assists = top_assists[top_assists["assists"].astype(float) > 0]
        if not top_assists.empty:
            st.markdown("**Top assisters**")
            st.dataframe(top_assists.head(20), use_container_width=True, hide_index=True)

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
# TAB 2 — Player Prices (futisporssi.fi)
# ═══════════════════════════════════════════════════════════════════════════════
with tab_prices:
    st.markdown("### Futispörssi Player Prices")
    st.caption(
        "Scrapes live player values from **futisporssi.fi** — no login required. "
        "Player prices change after each match day based on goals, assists, and popularity. "
        "Run after each game day to keep your `players.csv` values current. "
        "Player IDs are discovered gradually as players appear on public leaderboards — "
        "early in the tournament you'll see fewer players; coverage improves with each match day."
    )

    from pathlib import Path as _Path
    _id_store_path = _Path(__file__).parent.parent.parent / "Data" / "fp_player_ids.json"
    _known = 0
    if _id_store_path.exists():
        import json as _json
        try:
            _known = len(_json.loads(_id_store_path.read_text()))
        except Exception:
            pass

    st.info(
        f"**{_known} player IDs** currently in local store. "
        "IDs are discovered from public leaderboards — the store grows after each game day."
    )

    col_disc, col_full = st.columns(2)

    with col_disc:
        if st.button("🔍 Discover new player IDs only", help="Fast: scrapes leaderboard + team pages to find new player slugs (~1 min). Does not update prices."):
            with st.spinner("Scraping team pages for new player IDs…"):
                try:
                    store = discover_player_ids(delay=0.8)
                    st.success(f"Done — **{len(store)} player IDs** now in store.")
                except FPScrapeError as e:
                    st.error(f"Failed: {e}")

    with col_full:
        if st.button("💰 Discover + fetch all prices", type="primary", help="Full sync: discover IDs then fetch price for each (~3–5 min depending on how many players are known)."):
            players_df = load_csv("players.csv")
            progress_bar = st.progress(0.0, text="Starting…")
            status_text  = st.empty()

            def _price_progress(done, total, slug):
                pct = done / max(total, 1)
                progress_bar.progress(pct, text=f"Fetching {slug}… ({done}/{total})")
                status_text.caption(f"Last: **{slug}**")

            try:
                result = sync_prices(
                    players_df,
                    delay=1.0,
                    discover_new=True,
                    discover_delay=0.8,
                    progress_callback=_price_progress,
                )
            except FPScrapeError as e:
                st.error(f"Sync failed: {e}")
                st.stop()

            progress_bar.progress(1.0, text="Done!")
            status_text.empty()

            save_csv("players.csv", result["players"])

            st.success(
                f"**{result['prices_fetched']} prices fetched** from futisporssi.fi  ·  "
                f"{result['known_slugs']} player IDs in store"
            )
            if result["changes"]:
                st.markdown(f"**{len(result['changes'])} price changes:**")
                for c in result["changes"]:
                    st.markdown(f"- {c}")
            else:
                st.info("No price changes detected — values already up to date.")

    st.divider()

    # Show current squad prices
    st.markdown("### Current Player Values")
    _players = load_csv("players.csv")
    _mask = _players["in_squad"].astype(str).str.strip().str.lower() == "true"
    _squad = _players[_mask][["name", "position", "value"]].copy()
    if not _squad.empty:
        st.markdown("**My Squad**")
        st.dataframe(_squad, use_container_width=True, hide_index=True)

    with st.expander("All players by value"):
        _all = _players[["name", "position", "value"]].copy()
        st.dataframe(_all.sort_values("value", ascending=False), use_container_width=True, hide_index=True)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3 — Pre-tournament team form
# ═══════════════════════════════════════════════════════════════════════════════
with tab_form:
    st.markdown("### Pre-tournament Form Scraper")
    st.caption(
        "Fetches the last 5 completed matches for every WC 2026 team from Flashscore "
        "team result pages. This data is used to improve the model's initial attack/defence "
        "priors before any tournament results are available. "
        "**Run once before the tournament starts, then rely on Live Sync for updates.**"
    )

    existing_form = load_team_form()
    if not existing_form.empty:
        n_teams = existing_form["team"].nunique()
        n_matches = len(existing_form)
        st.success(f"**Form data loaded:** {n_teams} teams · {n_matches} match records")
        stats = get_form_stats(existing_form)
        gpg_map = stats.get("team_gpg", {})
        cg_map  = stats.get("team_concede_gpg", {})
        g_map   = stats.get("team_games", {})
        if gpg_map:
            with st.expander("Current form data by team"):
                rows = []
                for t in sorted(gpg_map.keys()):
                    rows.append({
                        "Team":            t,
                        "Matches":         g_map.get(t, 0),
                        "Goals/game":      round(gpg_map.get(t, 0), 2),
                        "Conceded/game":   round(cg_map.get(t, 0), 2),
                    })
                st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info("No form data yet — click **Fetch** to scrape it.")

    st.divider()

    n_matches_slider = st.slider(
        "Matches per team to fetch",
        min_value=3, max_value=8, value=5,
        help="Last N completed matches before the World Cup for each team.",
    )

    if st.button("📊 Fetch pre-tournament form (all 48 teams)", type="primary"):
        progress_bar = st.progress(0.0, text="Starting…")
        status_text  = st.empty()

        def _progress(done, total, team_name):
            pct = done / max(total, 1)
            progress_bar.progress(pct, text=f"Fetching {team_name}… ({done}/{total})")
            status_text.caption(f"Last fetched: **{team_name}**")

        try:
            df, errors = fetch_all_teams_form(
                n_matches=n_matches_slider,
                delay=1.5,
                progress_callback=_progress,
            )
        except ScrapeError as e:
            st.error(f"**Scrape failed:** {e}")
            st.stop()

        progress_bar.progress(1.0, text="Done!")
        status_text.empty()

        if errors:
            with st.expander(f"⚠️ {len(errors)} teams failed"):
                for e in errors:
                    st.warning(e)

        if df.empty:
            st.warning("No data returned. Check internet connection.")
        else:
            save_team_form(df)
            n_teams = df["team"].nunique()
            n_rows  = len(df)
            st.success(
                f"**Saved!** {n_teams} teams · {n_rows} match records → `Data/team_form.csv`"
            )
            stats = get_form_stats(df)
            gpg_map = stats.get("team_gpg", {})
            cg_map  = stats.get("team_concede_gpg", {})
            g_map   = stats.get("team_games", {})
            preview_rows = [
                {
                    "Team":          t,
                    "Matches":       g_map.get(t, 0),
                    "Goals/game":    round(gpg_map.get(t, 0), 2),
                    "Conceded/game": round(cg_map.get(t, 0), 2),
                }
                for t in sorted(gpg_map.keys())
            ]
            st.dataframe(preview_rows, use_container_width=True, hide_index=True)
            st.caption(
                "Reload the **Dashboard** or **Transfers** page to apply this data "
                "to the expected-points model."
            )

    st.divider()
    with st.expander("ℹ️ About this data"):
        st.markdown("""
**What gets scraped:** Flashscore team result pages — the last 5 completed matches
before June 11, 2026 for every team that appears in the WC 2026 group stage.

**Competition filter:** Flashscore does not label competition type in its data feed.
The results will be mostly WC qualification matches and continental competitions;
a small number of international friendlies may be included.

**How it affects the model:** The model starts with priors based on long-run
qualification stats (`form.csv`). Recent 5-match form blends in at 2× weight,
giving a better initial attack/defence estimate. Tournament results then take over
as games are played.

**xG:** Not available from Flashscore. If xG data is added later (e.g. from
FBref), it can be merged into `team_form.csv` using the `competition_id` column
as a match key.
        """)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3 — football-data.org
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
