"""
Flashscore.com scraper for WC 2026 — results, goalscorers, assists, and lineups.

No API key required. Parses Flashscore's proprietary ¬/~ delimited data
format embedded in every scoreboard and match-detail page.

Data flow:
  - Scoreboard page (flashscore.com/football/world/world-cup/) → all 104 WC
    match IDs, teams, scores.  Score keys: AG=home, AH=away.  Status 3=FT.
  - Incidents feed (www.flashscore.com/x/feed/df_sui_1_{id}) → goals + assists
    once a match has finished.
  - Lineups feed   (www.flashscore.com/x/feed/df_li_1_{id}) → confirmed XIs
    once a match has started or finished.

Storage (all local, nothing committed to git):
  - results.csv      — scores, goalscorers, assists per match
  - lineups.csv      — most-recent confirmed starting XI per team
  - player_stats.csv — per-player per-match: goals, assists, started, minutes
"""
from __future__ import annotations
import datetime
import re
import time
import urllib.request
import urllib.error
from collections import defaultdict

import pandas as pd

# ── Constants ─────────────────────────────────────────────────────────────────

WC_SCOREBOARD_URL  = "https://www.flashscore.com/football/world/world-cup/"
INCIDENTS_FEED_URL = "https://www.flashscore.com/x/feed/df_sui_1_{match_id}"
LINEUPS_FEED_URL   = "https://www.flashscore.com/x/feed/df_li_1_{match_id}"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept":   "*/*",
    "Referer":  "https://www.flashscore.com/",
    "X-Fsign":  "SW9D1eZo",
}

# Team name normalization: Flashscore names → app-standard names (from fixtures.csv / groups.csv)
_TEAM_NAME_MAP: dict[str, str] = {
    "south korea":          "Korea Republic",
    "czech republic":       "Czechia",
    "bosnia & herzegovina": "Bosnia and Herzegovina",
    "cape verde":           "Cabo Verde",
    "curacao":              "Curaçao",
    "d.r. congo":           "Congo DR",
    "iran":                 "IR Iran",
    "ivory coast":          "Côte d'Ivoire",
    "turkey":               "Türkiye",
}


def _normalize_team(name: str) -> str:
    return _TEAM_NAME_MAP.get(name.strip().lower(), name.strip())


# Flashscore status codes observed in WC 2026 feeds
# Scoreboard AB field: 1=scheduled, 3=FT/finished, 2/4/5=live variants
_STATUS_FINISHED = {"3", "6", "7"}   # 3=FT, 6=AET, 7=pens
_STATUS_LIVE     = {"2", "4", "5"}   # 2=1st half, 4=HT, 5=2nd half


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


# ── Block parsing ─────────────────────────────────────────────────────────────

def _parse_block_first(raw: str) -> dict[str, str]:
    """Parse ¬-delimited block into dict, keeping the FIRST value for each key."""
    out: dict[str, str] = {}
    for item in raw.replace("~", "").split("¬"):
        if "÷" in item:
            k, v = item.split("÷", 1)
            if k not in out:
                out[k] = v
    return out


def _parse_block_all(raw: str) -> dict[str, list[str]]:
    """Parse ¬-delimited block into dict, collecting ALL values per key (for repeated keys)."""
    out: dict[str, list[str]] = defaultdict(list)
    for item in raw.split("¬"):
        if "÷" in item:
            k, v = item.split("÷", 1)
            out[k].append(v)
    return dict(out)


def _blocks(body: str) -> list[str]:
    """Split body on ~ and return non-empty segments."""
    return [b for b in body.split("~") if b.strip()]


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
    seen_ids: set[str] = set()

    for raw in body.split("~AA÷")[1:]:
        f = _parse_block_first("AA÷" + raw)
        match_id = f.get("AA", "")
        if not match_id or match_id in seen_ids:
            continue
        seen_ids.add(match_id)

        home = f.get("CX") or f.get("AE", "")
        away = f.get("AF") or f.get("WN", "")

        if not home or not away:
            continue
        if any(home.startswith(p) for p in ("Winner", "1st", "2nd", "Best")):
            continue

        ts_raw = f.get("AD", "")
        date_str = ""
        if ts_raw:
            try:
                # WC 2026 is in North America — use EDT (UTC-4) so 9 pm EDT
                # kickoffs land on the correct local date.
                date_str = datetime.datetime.utcfromtimestamp(
                    int(ts_raw) - 4 * 3600
                ).strftime("%Y-%m-%d")
            except (ValueError, OSError):
                pass

        if date_str and date_str < "2026-06-01":
            continue

        # Scores: AG = home, AH = away (confirmed from live feeds)
        results.append({
            "match_id":   match_id,
            "home_team":  _normalize_team(home),
            "away_team":  _normalize_team(away),
            "date":       date_str,
            "round":      f.get("ER", ""),
            "status":     f.get("AB", ""),
            "home_score": f.get("AG", ""),
            "away_score": f.get("AH", ""),
        })

    return results


# ── Incidents feed: goals + assists ──────────────────────────────────────────

def _parse_incidents(body: str) -> list[dict]:
    """
    Parse the df_sui_1 incidents feed.

    Returns a list of goal dicts (own goals excluded from scorer list):
      scorer, assist, minute, team_side (1=home/2=away), type (goal/og/pen)
    """
    goals = []
    for blk in _blocks(body):
        vals = _parse_block_all(blk)

        # Only care about blocks that contain a Goal event (IK÷Goal)
        ik_values = vals.get("IK", [])
        if "Goal" not in ik_values:
            continue

        # IA: team side (1=home, 2=away) — first occurrence
        side    = vals["IA"][0] if vals.get("IA") else ""
        # IB: minute — first occurrence
        minute  = vals["IB"][0].rstrip("'") if vals.get("IB") else ""
        # IF: repeated — first = scorer, second = assister (if present)
        names   = vals.get("IF", [])
        scorer  = names[0] if names else ""
        assist  = names[1] if len(names) > 1 else ""
        # ICT: description — used to detect own goals
        desc    = vals["ICT"][0] if vals.get("ICT") else ""
        gtype   = "og" if "own goal" in desc.lower() else (
                  "pen" if "penalty" in desc.lower() else "goal"
        )

        if not scorer:
            continue
        goals.append({
            "scorer":    scorer,
            "assist":    assist,
            "minute":    minute,
            "team_side": side,
            "type":      gtype,
        })

    return goals


# ── Lineups feed: confirmed starting XIs ─────────────────────────────────────

def _parse_lineups(body: str, home_team: str = "", away_team: str = "") -> dict:
    """
    Parse the df_li_1 lineups feed.

    Returns:
      lineup_home, lineup_away  — lists of player dicts
      formation_home, formation_away — formation strings (e.g. "4-3-3")
    """
    lineup_home: list[dict] = []
    lineup_away: list[dict] = []
    formation_home = ""
    formation_away = ""
    teams_seen: list[str] = []   # ordered list of teams as first encountered
    home_team = _normalize_team(home_team)
    away_team = _normalize_team(away_team)

    for blk in _blocks(body):
        # Use first-value dict for simple lookups
        f = _parse_block_first(blk)

        name = f.get("LI", "")
        team = _normalize_team(f.get("LQ", ""))
        if not name or not team:
            continue

        # Track formation per team (LD field appears on first player block per team)
        if "LD" in f and team not in teams_seen:
            raw_fmt = f["LD"]
            # Strip leading "1-" GK digit if present (e.g. "1-4-3-3" → "4-3-3")
            if re.match(r"^1-", raw_fmt):
                raw_fmt = raw_fmt[2:]
            if not formation_home:
                formation_home = raw_fmt
            else:
                formation_away = raw_fmt
        if team not in teams_seen:
            teams_seen.append(team)

        starter  = f.get("LK", "")
        pos_raw  = f.get("LS", "")     # "Goalkeeper", "Captain", "" for most
        shirt    = f.get("LJ", "")
        minutes  = f.get("LO", "")     # minutes played (or 0 for unused sub)
        rating   = f.get("LPR", "")

        entry = {
            "name":     _clean_player_name(name),
            "position": _map_position_label(pos_raw),
            "shirt":    shirt,
            "starter":  "1" if starter == "1" else "0",
            "minutes":  minutes,
            "rating":   rating,
        }

        # Use team name from feed to assign home/away
        if team == home_team or (not lineup_home and team not in lineup_away):
            lineup_home.append(entry)
        else:
            lineup_away.append(entry)

    return {
        "lineup_home":    [p for p in lineup_home if p["starter"] == "1"],
        "lineup_away":    [p for p in lineup_away if p["starter"] == "1"],
        "all_home":       lineup_home,
        "all_away":       lineup_away,
        "formation_home": formation_home,
        "formation_away": formation_away,
    }


def _map_position_label(label: str) -> str:
    """Convert Flashscore position label to GK/DEF/MID/FWD."""
    label = label.strip().lower()
    if label in ("goalkeeper", "gk", "g"):
        return "GK"
    if label in ("defender", "cb", "lb", "rb", "rcb", "lcb", "d", "df",
                 "rwb", "lwb", "sw", "dc", "dl", "dr"):
        return "DEF"
    if label in ("midfielder", "cm", "dm", "am", "m", "mf", "cdm", "cam",
                 "lm", "rm", "wm", "mc", "ml", "mr", "dml", "dmr", "aml", "amr"):
        return "MID"
    if label in ("forward", "striker", "cf", "lw", "rw", "st", "f", "fw",
                 "ss", "fw", "fwl", "fwr", "amc"):
        return "FWD"
    return ""


def _clean_player_name(name: str) -> str:
    return re.sub(r"^\d+\s*", "", name).strip()


def _save_raw_feed(match_id: str, suffix: str, body: str) -> None:
    try:
        from pathlib import Path
        debug_dir = Path(__file__).parent.parent.parent / "Data" / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / f"fs_{suffix}_{match_id}.txt").write_text(body, encoding="utf-8")
    except Exception:
        pass


# ── Fetch one match's full detail ─────────────────────────────────────────────

def fetch_match_detail(match_id: str, home_team: str = "", away_team: str = "") -> dict | None:
    """
    Fetch incidents + lineups for one finished match.

    Returns None if either feed returns no data yet.
    Otherwise returns:
      home_score, away_score (from scoreboard — passed in as context if needed),
      goalscorers  — list of {scorer, assist, minute, team_side, type}
      lineup_home, lineup_away — lists of starter dicts
      formation_home, formation_away
      all_home, all_away — full squad (starters + subs)
    """
    incidents_url = INCIDENTS_FEED_URL.format(match_id=match_id)
    lineups_url   = LINEUPS_FEED_URL.format(match_id=match_id)

    inc_body = _get(incidents_url)
    lin_body = _get(lineups_url)

    if inc_body.strip() in ("0", "") and lin_body.strip() in ("0", ""):
        return None

    _save_raw_feed(match_id, "inc", inc_body)
    _save_raw_feed(match_id, "lin", lin_body)

    goals = _parse_incidents(inc_body) if inc_body.strip() not in ("0", "") else []
    lineup_data = (
        _parse_lineups(lin_body, home_team, away_team)
        if lin_body.strip() not in ("0", "")
        else {"lineup_home": [], "lineup_away": [], "all_home": [], "all_away": [],
              "formation_home": "", "formation_away": ""}
    )

    return {
        "goalscorers":    goals,
        "lineup_home":    lineup_data["lineup_home"],
        "lineup_away":    lineup_data["lineup_away"],
        "all_home":       lineup_data["all_home"],
        "all_away":       lineup_data["all_away"],
        "formation_home": lineup_data["formation_home"],
        "formation_away": lineup_data["formation_away"],
    }


# ── Build DataFrames ──────────────────────────────────────────────────────────

def build_results_df(
    matches: list[dict],
    details: dict[str, dict],
) -> pd.DataFrame:
    """
    Produce a DataFrame matching results.csv schema:
    match_id | date | home_team | away_team | home_score | away_score |
    goalscorers | assists
    """
    rows = []
    for m in matches:
        mid    = m["match_id"]
        detail = details.get(mid)

        hs  = m.get("home_score", "")
        as_ = m.get("away_score", "")

        scorer_str = ""
        assist_str = ""
        if detail and detail["goalscorers"]:
            scorers, assists = [], []
            for g in detail["goalscorers"]:
                if g["type"] == "og":
                    continue
                name = g["scorer"]
                min_ = g.get("minute", "")
                scorers.append(f"{name} {min_}'" if min_ else name)
                if g.get("assist"):
                    assists.append(f"{g['assist']} ({min_}')" if min_ else g["assist"])
            scorer_str = ", ".join(scorers)
            assist_str = ", ".join(assists)

        rows.append({
            "match_id":    mid,
            "date":        m["date"],
            "home_team":   m["home_team"],
            "away_team":   m["away_team"],
            "home_score":  hs,
            "away_score":  as_,
            "goalscorers": scorer_str,
            "assists":     assist_str,
        })

    return pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=["match_id", "date", "home_team", "away_team",
                 "home_score", "away_score", "goalscorers", "assists"]
    )


def build_lineups_df(
    matches: list[dict],
    details: dict[str, dict],
) -> pd.DataFrame:
    """
    Build an updated lineups DataFrame from confirmed starters.
    Each team's lineup is overwritten with the most recent confirmed data.
    Columns: team | player_name | position | formation
    """
    team_lineups: dict[str, dict] = {}

    for m in matches:
        mid    = m["match_id"]
        detail = details.get(mid)
        if not detail:
            continue

        home_team = m["home_team"]
        away_team = m["away_team"]

        if detail["lineup_home"]:
            team_lineups[_normalize_team(home_team)] = {
                "players":   detail["lineup_home"],
                "formation": detail.get("formation_home", ""),
            }
        if detail["lineup_away"]:
            team_lineups[_normalize_team(away_team)] = {
                "players":   detail["lineup_away"],
                "formation": detail.get("formation_away", ""),
            }

    rows = []
    for team, info in team_lineups.items():
        fmt = info["formation"]
        for p in info["players"]:
            rows.append({
                "team":        team,
                "player_name": p["name"],
                "position":    p.get("position", "") or _infer_position_from_shirt(p),
                "formation":   fmt,
            })

    if not rows:
        return pd.DataFrame(columns=["team", "player_name", "position", "formation"])

    df = pd.DataFrame(rows)
    df = _enrich_lineup_positions(df)
    return df


def _enrich_lineup_positions(df: pd.DataFrame) -> pd.DataFrame:
    """Fill empty position cells by looking up the player in players.csv."""
    missing = df["position"].isna() | (df["position"].astype(str).str.strip() == "")
    if not missing.any():
        return df

    try:
        from pathlib import Path as _Path
        _players_path = _Path(__file__).parent.parent.parent / "Data" / "players.csv"
        if not _players_path.exists():
            return df
        _pdf = pd.read_csv(_players_path, dtype=str)
        _pdf.columns = [c.strip().lower() for c in _pdf.columns]
        if "name" not in _pdf.columns or "position" not in _pdf.columns:
            return df
        # Build lookup: full name and lastname+initial → position
        _pos_map: dict = {}
        _initial_map: dict = {}
        for _n, _p in zip(_pdf["name"], _pdf["position"]):
            _n, _p = str(_n).strip(), str(_p).strip().upper()
            if not _p or _p == "NAN":
                continue
            _pos_map[_n.lower()] = _p
            _parts = _n.split()
            if len(_parts) >= 2:
                _key = _parts[0].lower() + "_" + _parts[1][0].lower()
                _initial_map.setdefault(_key, _p)

        def _lookup(row):
            if str(row["position"]).strip() not in ("", "nan"):
                return row["position"]
            _pn = str(row["player_name"]).strip()
            # Exact match
            hit = _pos_map.get(_pn.lower())
            if hit:
                return hit
            # Lastname I. format
            _pp = _pn.split()
            if len(_pp) >= 2:
                _k = _pp[0].lower().rstrip(".") + "_" + _pp[1].replace(".", "").lower()[:1]
                hit = _initial_map.get(_k)
                if hit:
                    return hit
            return ""
        df["position"] = df.apply(_lookup, axis=1)
    except Exception:
        pass
    return df


def build_player_stats_df(
    matches: list[dict],
    details: dict[str, dict],
) -> pd.DataFrame:
    """
    Build a per-player per-match stats DataFrame.
    Columns: match_id | date | player_name | team | opponent |
             goals | assists | started | minutes | position
    """
    rows = []
    for m in matches:
        mid    = m["match_id"]
        detail = details.get(mid)
        if not detail:
            continue

        home_team = m["home_team"]
        away_team = m["away_team"]
        date      = m["date"]

        # Goal and assist counts per player name
        goal_counts:   dict[str, int] = defaultdict(int)
        assist_counts: dict[str, int] = defaultdict(int)
        goal_sides:    dict[str, str] = {}

        for g in detail["goalscorers"]:
            if g["type"] == "og":
                continue
            goal_counts[g["scorer"]] += 1
            goal_sides[g["scorer"]]   = g.get("team_side", "")
            if g.get("assist"):
                assist_counts[g["assist"]] += 1

        for side_label, squad, team, opp in [
            ("home", detail.get("all_home", detail["lineup_home"]), _normalize_team(home_team), _normalize_team(away_team)),
            ("away", detail.get("all_away", detail["lineup_away"]), _normalize_team(away_team), _normalize_team(home_team)),
        ]:
            for p in squad:
                name = p["name"]
                rows.append({
                    "match_id":  mid,
                    "date":      date,
                    "player_name": name,
                    "team":      team,
                    "opponent":  opp,
                    "goals":     goal_counts.get(name, 0),
                    "assists":   assist_counts.get(name, 0),
                    "started":   1 if p.get("starter") == "1" else 0,
                    "minutes":   _safe_int(p.get("minutes", "")),
                    "position":  p.get("position", ""),
                })

    if not rows:
        return pd.DataFrame(columns=[
            "match_id", "date", "player_name", "team", "opponent",
            "goals", "assists", "started", "minutes", "position"
        ])
    return pd.DataFrame(rows)


def _safe_int(val: str) -> int:
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0


def _infer_position_from_shirt(p: dict) -> str:
    shirt = _safe_int(p.get("shirt", ""))
    if shirt == 1:
        return "GK"
    return ""


# ── Merge helpers ─────────────────────────────────────────────────────────────

def merge_results(existing: pd.DataFrame, scraped: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """
    Merge scraped results into existing results.csv.
    Returns (merged DataFrame, list of change strings).
    """
    if scraped.empty:
        return existing, []

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
                merged_row = row.to_dict()
                merged_row["home_score"]  = hs_new
                merged_row["away_score"]  = as_new
                merged_row["goalscorers"] = new.get("goalscorers", row.get("goalscorers", ""))
                merged_row["assists"]     = new.get("assists", row.get("assists", ""))
                updated.append(merged_row)
                continue
        updated.append(row.to_dict())

    # Ensure assists column exists in output
    df = pd.DataFrame(updated)
    if "assists" not in df.columns:
        df["assists"] = ""
    return df, changes


def merge_lineups(existing: pd.DataFrame, scraped: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """
    Merge scraped lineups into existing lineups.csv.
    Teams with new data are fully replaced; others are kept.
    Returns (merged DataFrame, list of teams updated).
    """
    if scraped.empty:
        return existing, []

    updated_teams = set(scraped["team"].astype(str).str.strip().tolist())
    kept    = existing[~existing["team"].astype(str).str.strip().isin(updated_teams)].copy()
    merged  = pd.concat([kept, scraped], ignore_index=True)
    return merged, sorted(updated_teams)


def merge_player_stats(existing: pd.DataFrame, scraped: pd.DataFrame) -> pd.DataFrame:
    """
    Merge scraped player stats into existing player_stats.csv.
    Match-level data is replaced wholesale; other matches are kept.
    """
    if scraped.empty:
        return existing

    if existing.empty:
        return scraped

    scraped_match_ids = set(scraped["match_id"].astype(str).tolist())
    kept = existing[~existing["match_id"].astype(str).isin(scraped_match_ids)].copy()
    return pd.concat([kept, scraped], ignore_index=True)


# ── Main sync entry point ─────────────────────────────────────────────────────

def sync_flashscore(
    existing_results: pd.DataFrame,
    existing_lineups: pd.DataFrame,
    existing_player_stats: pd.DataFrame | None = None,
    fetch_details: bool = True,
    delay_seconds: float = 1.5,
) -> dict:
    """
    Full sync:
      1. Fetch all WC 2026 match IDs and scores from the scoreboard.
      2. For each finished match, fetch incidents (goals) + lineups.
      3. Merge into existing DataFrames.

    Returns:
      results, lineups, player_stats — updated DataFrames
      result_changes  — list of "Home X–Y Away" strings
      lineup_changes  — list of team names updated
      matches_fetched — int
      details_parsed  — int
      errors          — list of error strings
    """
    matches  = fetch_all_matches()
    finished = [m for m in matches if m["status"] in _STATUS_FINISHED]

    details: dict[str, dict] = {}
    errors:  list[str]       = []

    if fetch_details and finished:
        for i, m in enumerate(finished):
            mid = m["match_id"]
            try:
                detail = fetch_match_detail(
                    mid,
                    home_team=m["home_team"],
                    away_team=m["away_team"],
                )
                if detail:
                    details[mid] = detail
            except ScrapeError as e:
                errors.append(f"{m['home_team']} vs {m['away_team']}: {e}")
            if i < len(finished) - 1:
                time.sleep(delay_seconds)

    scraped_results      = build_results_df(matches, details)
    scraped_lineups      = build_lineups_df(matches, details)
    scraped_player_stats = build_player_stats_df(matches, details)

    merged_results, result_changes = merge_results(existing_results, scraped_results)
    merged_lineups, lineup_changes = merge_lineups(existing_lineups, scraped_lineups)
    merged_player_stats = merge_player_stats(
        existing_player_stats if existing_player_stats is not None else pd.DataFrame(),
        scraped_player_stats,
    )

    return {
        "results":          merged_results,
        "lineups":          merged_lineups,
        "player_stats":     merged_player_stats,
        "result_changes":   result_changes,
        "lineup_changes":   lineup_changes,
        "matches_fetched":  len(finished),
        "details_parsed":   len(details),
        "errors":           errors,
    }
