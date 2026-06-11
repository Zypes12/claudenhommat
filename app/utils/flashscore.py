"""
Flashscore.com scraper for WC 2026 — results, goalscorers, and lineups.

No API key required. Parses Flashscore's proprietary ¬/~ delimited data
format embedded in every scoreboard and match-detail page.

Data flow:
  - Scoreboard page (flashscore.com/football/world/world-cup/) contains all
    104 WC matches with IDs, scores, and round labels.
  - Detail feed (d.flashscore.com/x/feed/dc_1_{match_id}) returns full match
    data including confirmed lineups once a match has started or finished.
    Pre-match it returns the single character '0'.

Storage (all local, nothing committed to git):
  - results.csv  — updated with scores and goalscorers after each game
  - lineups.csv  — updated with confirmed starting XIs after each game
"""
from __future__ import annotations
import datetime
import re
import time
import urllib.request
import urllib.error

import pandas as pd

# ── Constants ─────────────────────────────────────────────────────────────────

WC_SCOREBOARD_URL = "https://www.flashscore.com/football/world/world-cup/"
DETAIL_FEED_URL   = "https://d.flashscore.com/x/feed/dc_1_{match_id}"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept":   "*/*",
    "Referer":  "https://www.flashscore.com/",
    "X-Fsign":  "SW9D1eZo",
}

# ── Flashscore field-code reference ──────────────────────────────────────────
# Scoreboard records (split on ~AA÷):
#   AA = match_id          CX / WM = home team     AF / WN = away team
#   AD = unix timestamp    AB = status (1=sched, 2=live, 6=finished)
#   DR = home score        DS = away score          ER = round label
#
# Detail feed (dc_1_{id}) — populated once match starts/finishes:
#   Match header follows same keys above.
#   Goal events (split on ~IN÷):
#     IN = scorer name      IO = assist name        IP = minute
#     IQ = goal type  (1=regular, 2=penalty, 3=own goal)
#     IR = team side  (1=home, 2=away)
#   Lineup entries (split on ~PA÷ or ~PB÷):
#     PA / PB = player name (PA=home XI, PB=away XI — varies by version)
#     Some versions use ~IL÷ for lineup blocks with PA=name, PB=position
#     Field codes vary slightly across Flashscore versions; the parser below
#     probes multiple known patterns and saves raw feed for debugging.

_STATUS_FINISHED = {"6", "7"}   # 6=full time, 7=after extra time / penalties
_STATUS_LIVE     = {"2", "3", "4", "5"}   # in play / HT / ET / pen


class ScrapeError(Exception):
    """Raised with a user-friendly message when scraping fails."""


# ── Low-level HTTP ────────────────────────────────────────────────────────────

def _get(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        raise ScrapeError(f"HTTP {e.code} fetching {url}")
    except urllib.error.URLError as e:
        raise ScrapeError(f"Network error: {e.reason}")


# ── Flashscore format parser ──────────────────────────────────────────────────

def _parse_record(raw: str) -> dict[str, str]:
    """Parse one ¬-delimited Flashscore record into a {key: value} dict."""
    return {
        k: v
        for item in raw.replace("~", "").split("¬")
        if "÷" in item
        for k, v in [item.split("÷", 1)]
    }


# ── Scoreboard: all WC matches ────────────────────────────────────────────────

def fetch_all_matches() -> list[dict]:
    """
    Return all WC 2026 matches from the Flashscore scoreboard page.

    Each dict contains:
      match_id, home_team, away_team, date (YYYY-MM-DD),
      round, status, home_score, away_score
    """
    body = _get(WC_SCOREBOARD_URL)
    results = []

    for raw in body.split("~AA÷")[1:]:
        # Prepend the key so _parse_record can find AA=match_id
        f = _parse_record("AA÷" + raw)
        match_id = f.get("AA", "")
        if not match_id:
            continue

        home = f.get("CX") or f.get("WM", "")
        away = f.get("AF") or f.get("WN", "")

        # Skip placeholder knockout rows
        if not home or not away:
            continue
        if any(home.startswith(p) for p in ("Winner", "1st", "2nd", "Best")):
            continue

        ts_raw = f.get("AD", "")
        date_str = ""
        if ts_raw:
            try:
                # WC 2026 games are in North America. Use EDT (UTC-4) so that a
                # 9 pm EDT kickoff (01:00 UTC next day) lands on the right local date.
                edt_offset = 4 * 3600
                date_str = datetime.datetime.utcfromtimestamp(
                    int(ts_raw) - edt_offset
                ).strftime("%Y-%m-%d")
            except (ValueError, OSError):
                pass

        # Flashscore's WC page includes qualifying playoff rows dated 2025 — skip them.
        if date_str and date_str < "2026-06-01":
            continue

        results.append({
            "match_id":   match_id,
            "home_team":  home,
            "away_team":  away,
            "date":       date_str,
            "round":      f.get("ER", ""),
            "status":     f.get("AB", ""),
            "home_score": f.get("DR", ""),
            "away_score": f.get("DS", ""),
        })

    return results


# ── Detail feed: goals + lineups for one match ────────────────────────────────

def fetch_match_detail(match_id: str) -> dict | None:
    """
    Fetch the detail feed for one match.

    Returns None if the match hasn't started yet (feed returns '0').
    Otherwise returns:
      home_score, away_score,
      goalscorers (list of {name, team_side, minute, type}),
      lineup_home (list of {name, position, shirt}),
      lineup_away (list of {name, position, shirt}),
      raw (the raw feed string — useful for debugging new field codes)
    """
    url = DETAIL_FEED_URL.format(match_id=match_id)
    body = _get(url)

    if not body or body.strip() in ("0", ""):
        return None  # pre-match — no data yet

    # Save raw for debugging (only if Data/ is writable)
    _save_raw_feed(match_id, body)

    result: dict = {
        "raw":          body,
        "home_score":   "",
        "away_score":   "",
        "goalscorers":  [],
        "lineup_home":  [],
        "lineup_away":  [],
        "formation_home": "",
        "formation_away": "",
    }

    # ── Header ────────────────────────────────────────────────────────────────
    header_raw = body.split("~AA÷")[1].split("~")[0] if "~AA÷" in body else ""
    if header_raw:
        h = _parse_record(header_raw)
        result["home_score"] = h.get("DR", "")
        result["away_score"] = h.get("DS", "")
        result["formation_home"] = h.get("DP", "")
        result["formation_away"] = h.get("DQ", "")

    # ── Goals ──────────────────────────────────────────────────────────────────
    # Goal entries are marked with ~IN÷ (Incident iN?)
    # Known codes: IN=scorer, IO=assist, IP=minute, IQ=type, IR=team side
    for segment in body.split("~IN÷")[1:]:
        f = _parse_record(segment)
        name    = f.get("IN", "")
        assist  = f.get("IO", "")
        minute  = f.get("IP", "")
        gtype   = f.get("IQ", "1")   # 1=regular, 2=pen, 3=own goal
        side    = f.get("IR", "")    # 1=home, 2=away

        if not name:
            continue
        result["goalscorers"].append({
            "name":      name,
            "assist":    assist,
            "minute":    minute,
            "type":      gtype,
            "team_side": side,
        })

    # ── Lineups ────────────────────────────────────────────────────────────────
    # Flashscore lineup blocks vary by version. We probe three known patterns:
    #   Pattern A: ~PA÷{player}¬ for home, ~PB÷{player}¬ for away
    #   Pattern B: ~IL÷ blocks with sub-fields
    #   Pattern C: ~AT÷{player}¬AV÷{team_side}¬ style

    # Pattern A — most common in recent versions
    if "~PA÷" in body or "~PB÷" in body:
        for seg in body.split("~PA÷")[1:]:
            f = _parse_record(seg)
            name = f.get("PA", seg.split("¬")[0])
            result["lineup_home"].append({
                "name":     _clean_player_name(name),
                "position": f.get("PC", ""),
                "shirt":    f.get("PD", ""),
                "starter":  f.get("PE", "1"),
            })
        for seg in body.split("~PB÷")[1:]:
            f = _parse_record(seg)
            name = f.get("PB", seg.split("¬")[0])
            result["lineup_away"].append({
                "name":     _clean_player_name(name),
                "position": f.get("PC", ""),
                "shirt":    f.get("PD", ""),
                "starter":  f.get("PE", "1"),
            })

    # Pattern C — fallback (AT÷name, AV÷side)
    elif "~AT÷" in body:
        for seg in body.split("~AT÷")[1:]:
            f = _parse_record(seg)
            name = f.get("AT", seg.split("¬")[0])
            side = f.get("AV", "")
            entry = {
                "name":     _clean_player_name(name),
                "position": f.get("AX", ""),
                "shirt":    f.get("AY", ""),
                "starter":  f.get("AZ", "1"),
            }
            if side == "1":
                result["lineup_home"].append(entry)
            elif side == "2":
                result["lineup_away"].append(entry)

    return result


def _clean_player_name(name: str) -> str:
    """Strip shirt numbers or other noise sometimes attached to names."""
    return re.sub(r"^\d+\s*", "", name).strip()


def _save_raw_feed(match_id: str, body: str) -> None:
    """Save raw detail feed to Data/debug/ for field-code inspection."""
    try:
        from pathlib import Path
        debug_dir = Path(__file__).parent.parent.parent / "Data" / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / f"fs_detail_{match_id}.txt").write_text(body, encoding="utf-8")
    except Exception:
        pass


# ── Build DataFrames ──────────────────────────────────────────────────────────

def build_results_df(
    matches: list[dict],
    details: dict[str, dict],  # match_id → detail dict
) -> pd.DataFrame:
    """
    Combine scoreboard match list with detail data to produce a DataFrame
    matching our results.csv schema:
    match_id | date | home_team | away_team | home_score | away_score | goalscorers
    """
    rows = []
    for m in matches:
        mid    = m["match_id"]
        detail = details.get(mid)

        hs = detail["home_score"] if detail else m.get("home_score", "")
        as_ = detail["away_score"] if detail else m.get("away_score", "")

        # Goalscorer string: "Lozano 23', Jimenez 67'"
        scorer_str = ""
        if detail and detail["goalscorers"]:
            parts = []
            for g in detail["goalscorers"]:
                if g["type"] == "3":   # own goal — skip for expected-pts model
                    continue
                name = g["name"]
                min_ = g.get("minute", "")
                parts.append(f"{name} {min_}'" if min_ else name)
            scorer_str = ", ".join(parts)

        rows.append({
            "match_id":    mid,
            "date":        m["date"],
            "home_team":   m["home_team"],
            "away_team":   m["away_team"],
            "home_score":  hs,
            "away_score":  as_,
            "goalscorers": scorer_str,
        })

    return pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=["match_id", "date", "home_team", "away_team",
                 "home_score", "away_score", "goalscorers"]
    )


def build_lineups_df(
    matches: list[dict],
    details: dict[str, dict],
) -> pd.DataFrame:
    """
    Build an updated lineups DataFrame from confirmed starters.

    Only includes players marked as starters (starter == '1').
    Each team keeps only its MOST RECENTLY confirmed lineup — so after
    Matchday 2 the lineups for Matchday 1 are overwritten.
    Result columns match lineups.csv: team | player_name | position | formation
    """
    # latest confirmed lineup per team (match list is in date order)
    team_lineups: dict[str, dict] = {}   # team → {players, formation}

    for m in matches:
        mid    = m["match_id"]
        detail = details.get(mid)
        if not detail:
            continue
        if not detail["lineup_home"] and not detail["lineup_away"]:
            continue

        home_team = m["home_team"]
        away_team = m["away_team"]

        if detail["lineup_home"]:
            team_lineups[home_team] = {
                "players":   [p for p in detail["lineup_home"] if p.get("starter") != "0"],
                "formation": detail.get("formation_home", ""),
            }
        if detail["lineup_away"]:
            team_lineups[away_team] = {
                "players":   [p for p in detail["lineup_away"] if p.get("starter") != "0"],
                "formation": detail.get("formation_away", ""),
            }

    rows = []
    for team, info in team_lineups.items():
        fmt = info["formation"]
        for p in info["players"]:
            rows.append({
                "team":        team,
                "player_name": p["name"],
                "position":    _map_position(p.get("position", "")),
                "formation":   fmt,
            })

    return pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=["team", "player_name", "position", "formation"]
    )


def _map_position(fs_pos: str) -> str:
    """Map Flashscore position codes to our GK/DEF/MID/FWD schema."""
    mapping = {
        "G": "GK", "GK": "GK",
        "D": "DEF", "DF": "DEF", "CB": "DEF", "LB": "DEF", "RB": "DEF",
        "M": "MID", "MF": "MID", "CM": "MID", "DM": "MID", "AM": "MID",
        "F": "FWD", "FW": "FWD", "CF": "FWD", "LW": "FWD", "RW": "FWD",
        "ST": "FWD",
    }
    return mapping.get(str(fs_pos).upper().strip(), fs_pos or "")


# ── Merge helpers ─────────────────────────────────────────────────────────────

def merge_results(existing: pd.DataFrame, scraped: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """
    Merge scraped results into existing results.csv.
    Returns (merged DataFrame, list of change strings).
    """
    if scraped.empty:
        return existing, []

    # Build lookup by (home_team, away_team)
    lookup = {
        (str(r["home_team"]).strip().lower(), str(r["away_team"]).strip().lower()): r.to_dict()
        for _, r in scraped.iterrows()
    }

    changes: list[str] = []
    updated = []
    for _, row in existing.iterrows():
        key = (str(row["home_team"]).strip().lower(), str(row["away_team"]).strip().lower())
        if key in lookup:
            new = lookup[key]
            hs_new = str(new.get("home_score", "")).strip()
            as_new = str(new.get("away_score", "")).strip()
            hs_old = str(row.get("home_score", "")).strip()
            as_old = str(row.get("away_score", "")).strip()
            if hs_new and as_new and (hs_new != hs_old or as_new != as_old):
                changes.append(
                    f"{row['home_team']} {hs_new}–{as_new} {row['away_team']}"
                )
            if hs_new and as_new:
                updated.append({**row.to_dict(), "home_score": hs_new, "away_score": as_new,
                                 "goalscorers": new.get("goalscorers", row.get("goalscorers", ""))})
                continue
        updated.append(row.to_dict())

    return pd.DataFrame(updated), changes


def merge_lineups(existing: pd.DataFrame, scraped: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """
    Merge scraped confirmed lineups into existing lineups.csv.
    Teams with new lineup data are fully replaced; teams not in scraped are kept.
    Returns (merged DataFrame, list of teams updated).
    """
    if scraped.empty:
        return existing, []

    updated_teams = set(scraped["team"].astype(str).str.strip().tolist())

    # Keep existing rows for teams NOT in the new lineup data
    kept = existing[~existing["team"].astype(str).str.strip().isin(updated_teams)].copy()
    merged = pd.concat([kept, scraped], ignore_index=True)
    changes = sorted(updated_teams)
    return merged, changes


# ── Main sync entry point ─────────────────────────────────────────────────────

def sync_flashscore(
    existing_results: pd.DataFrame,
    existing_lineups: pd.DataFrame,
    fetch_lineups: bool = True,
    delay_seconds: float = 1.5,
) -> dict:
    """
    Full sync:
      1. Fetch all WC 2026 match IDs and scores from the scoreboard.
      2. For each finished match, fetch the detail feed (lineups + goals).
      3. Merge into existing results.csv and lineups.csv DataFrames.

    Returns:
      results   – updated results DataFrame
      lineups   – updated lineups DataFrame
      result_changes  – list of "Home X–Y Away" strings
      lineup_changes  – list of team names whose lineup was updated
      matches_fetched – int
      errors          – list of error messages for failed detail fetches
    """
    # Step 1: scoreboard
    matches = fetch_all_matches()
    finished = [m for m in matches if m["status"] in _STATUS_FINISHED]

    # Step 2: detail feeds for finished matches
    details: dict[str, dict] = {}
    errors: list[str] = []

    if fetch_lineups and finished:
        for i, m in enumerate(finished):
            mid = m["match_id"]
            try:
                detail = fetch_match_detail(mid)
                if detail:
                    details[mid] = detail
            except ScrapeError as e:
                errors.append(f"{m['home_team']} vs {m['away_team']}: {e}")
            # Polite delay between requests to avoid rate limiting
            if i < len(finished) - 1:
                time.sleep(delay_seconds)

    # Step 3: build and merge
    scraped_results = build_results_df(matches, details)
    scraped_lineups = build_lineups_df(matches, details)

    merged_results, result_changes  = merge_results(existing_results, scraped_results)
    merged_lineups, lineup_changes  = merge_lineups(existing_lineups, scraped_lineups)

    return {
        "results":          merged_results,
        "lineups":          merged_lineups,
        "result_changes":   result_changes,
        "lineup_changes":   lineup_changes,
        "matches_fetched":  len(finished),
        "details_parsed":   len(details),
        "errors":           errors,
    }
