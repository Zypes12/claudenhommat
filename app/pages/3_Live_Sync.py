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
    build_results_df, merge_into_results,
    APIError,
)

st.set_page_config(page_title="Live Sync", page_icon="🔗", layout="wide")
st.title("🔗 Live Data Sync")
st.caption(
    "Fetches real match results and goalscorers from **football-data.org** "
    "and writes them to your local results.csv automatically. "
    "The recommendation model then picks up the new data on the next page load."
)

# ── API key setup ─────────────────────────────────────────────────────────────
st.markdown("## 1 — API Key")

stored_key = load_api_key()
with st.expander(
    "Configure API key" if not stored_key else "✅  API key configured  (click to change)",
    expanded=not stored_key,
):
    st.markdown(
        "The free tier is all you need. Registration takes about 2 minutes:\n\n"
        "1. Go to **https://www.football-data.org/client/register**\n"
        "2. Enter your email and a password\n"
        "3. Check your inbox for the confirmation email — your API key is inside\n"
        "4. Paste the key below and click **Save**"
    )
    key_input = st.text_input(
        "Paste your API key here",
        value=stored_key,
        type="password",
        placeholder="e.g.  a1b2c3d4e5f6...",
    )
    col_save, col_test = st.columns([1, 2])
    with col_save:
        if st.button("💾 Save key"):
            save_api_key(key_input)
            st.success("Key saved.")
            st.rerun()
    with col_test:
        if st.button("🔌 Test connection"):
            with st.spinner("Connecting…"):
                ok, msg = test_connection(key_input or stored_key)
            if ok:
                st.success(msg)
            else:
                st.error(msg)

api_key = load_api_key()

st.divider()

# ── Sync results ──────────────────────────────────────────────────────────────
st.markdown("## 2 — Sync Results")

last_sync = load_last_sync()
if last_sync:
    try:
        ts = datetime.datetime.fromisoformat(last_sync)
        age_min = int((datetime.datetime.now() - ts).total_seconds() / 60)
        age_str = f"{age_min} min ago" if age_min < 60 else f"{age_min // 60}h ago"
        st.caption(f"Last synced: **{ts.strftime('%Y-%m-%d %H:%M')}** ({age_str})")
    except ValueError:
        st.caption(f"Last synced: {last_sync}")
else:
    st.caption("Never synced yet.")

st.markdown(
    "Clicking **Sync now** makes one API request, fetches all WC 2026 results "
    "that have been played, and updates your local `results.csv`. "
    "The model's expected-points calculations update on the next page load."
)

if not api_key:
    st.warning("⚠️  Set your API key above before syncing.")
    st.stop()

if st.button("🔄 Sync now", type="primary"):
    with st.spinner("Fetching from football-data.org…"):
        try:
            matches   = fetch_wc_matches(api_key)
            fetched   = build_results_df(matches)
            existing  = load_csv("results.csv")
            merged, changes = merge_into_results(existing, fetched)
        except APIError as e:
            st.error(f"**Sync failed:** {e}")
            st.stop()

    if not changes:
        st.info("Everything is already up to date — no new results.")
    else:
        st.success(f"**{len(changes)} result(s) updated:**")
        for c in changes:
            st.markdown(f"- {c}")

    save_csv("results.csv", merged)
    now_str = datetime.datetime.now().isoformat(timespec="seconds")
    save_last_sync(now_str)
    st.caption(f"Saved at {now_str}")

st.divider()

# ── Preview current results ───────────────────────────────────────────────────
st.markdown("## 3 — Current Results")
st.caption("Matches that have been played and are in your local results.csv.")

results = load_csv("results.csv")
played = results[
    results["home_score"].astype(str).str.strip().notna()
    & (results["home_score"].astype(str).str.strip() != "")
    & (results["home_score"].astype(str).str.strip() != "nan")
].copy()

if played.empty:
    st.info("No results recorded yet. Sync once the tournament is underway.")
else:
    played_display = played[["date", "home_team", "home_score", "away_score", "away_team", "goalscorers"]].copy()
    played_display.columns = ["Date", "Home", "Home score", "Away score", "Away", "Goalscorers"]
    st.dataframe(played_display, use_container_width=True, hide_index=True)
    st.caption(f"**{len(played)} matches played** · {len(results) - len(played)} still to come")

st.divider()

# ── Model impact preview ──────────────────────────────────────────────────────
st.markdown("## 4 — Model Impact")
st.caption(
    "How many games of actual data the model has per team. "
    "At 0 games the model uses pre-tournament priors only. "
    "After 5 games the blend is 50% actual / 50% prior."
)

if not played.empty:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from logic.recommendations import compute_actual_stats

    actual = compute_actual_stats(results)
    team_games = actual.get("team_games", {})

    if team_games:
        rows = []
        for t, g in sorted(team_games.items(), key=lambda x: -x[1]):
            atk  = actual["team_attack"].get(t, 0)
            defe = actual["team_defense"].get(t, 0)
            blend = round(g / (5 + g) * 100)
            rows.append({
                "Team":          t,
                "Games played":  g,
                "Actual GPG":    round(atk, 2),
                "Conceded/game": round(defe, 2),
                "Model blend":   f"{blend}% actual",
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)

        player_goals = actual.get("player_goals", {})
        if player_goals:
            st.markdown("**Goalscorers tracked:**")
            top = sorted(player_goals.items(), key=lambda x: -x[1])[:15]
            scorer_rows = [{"Player (normalised)": k, "Goals": v} for k, v in top]
            st.dataframe(scorer_rows, use_container_width=True, hide_index=True)
    else:
        st.info("No team stats yet — sync results first.")
else:
    st.info("No played matches in results.csv yet.")
