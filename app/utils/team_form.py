"""
Pre-tournament team form scraper.

Fetches the last N completed matches for every WC 2026 team from
Flashscore team result pages. Stores data in Data/team_form.csv.

Data fields per match row:
  team | date | competition_id | opponent | home_away | goals_for |
  goals_against | result (W/D/L) | match_id

Note: Flashscore team pages include both competitive and friendly matches.
      Competition type is not embedded in the feed; competition_id is an
      opaque Flashscore ID. WC qualification matches cluster together by
      competition_id — the first few distinct IDs for a team are typically
      their qualifiers.

Usage:
    from utils.team_form import fetch_all_teams_form, get_form_stats
    df = fetch_all_teams_form(n_matches=5, delay=1.5)
    stats = get_form_stats(df)  # feeds into the model
"""
from __future__ import annotations

import datetime
import time
from pathlib import Path

import pandas as pd

from utils.flashscore import _get, _parse_record, WC_SCOREBOARD_URL, ScrapeError

# ── Constants ─────────────────────────────────────────────────────────────────

# First WC match date — only fetch pre-tournament form
_WC_START = "2026-06-11"

# Dedup: max rows per team (fetch a few more then slice)
_FETCH_PER_TEAM = 12

FORM_COLUMNS = [
    "team", "date", "competition_id", "opponent",
    "home_away", "goals_for", "goals_against", "result", "match_id",
]


# ── Step 1: Extract all 48 WC team slugs + IDs from the scoreboard ───────────

def extract_wc_team_ids() -> dict[str, tuple[str, str]]:
    """
    Return {display_name: (flashscore_slug, flashscore_id)} for the 48 WC 2026 teams.

    Uses fetch_all_matches() which already filters to matches on/after 2026-06-01,
    so only the actual 48 tournament participants appear (not teams from qualifying
    playoff matches that Flashscore also lists on the WC page).

    Field codes confirmed from live scoreboard:
      CX / AE = home team name   WU = home slug   PX = home ID
      AF / FK = away team name   WV = away slug   PY = away ID
    """
    body = _get(WC_SCOREBOARD_URL)
    teams: dict[str, tuple[str, str]] = {}

    for raw in body.split("~AA÷")[1:]:
        f = _parse_record("AA÷" + raw)

        # Apply the same date filter as fetch_all_matches() — skip pre-June-2026 rows
        ts_raw = f.get("AD", "")
        if ts_raw and ts_raw.isdigit():
            try:
                import datetime as _dt
                date_str = _dt.datetime.utcfromtimestamp(int(ts_raw)).strftime("%Y-%m-%d")
                if date_str < "2026-06-01":
                    continue
            except (ValueError, OSError):
                pass

        home_name = f.get("CX") or f.get("AE", "")
        home_slug = f.get("WU", "")
        home_id   = f.get("PX", "")

        away_name = f.get("AF") or f.get("FK", "")
        away_slug = f.get("WV", "")
        away_id   = f.get("PY", "")

        if home_name and home_slug and home_id:
            teams[home_name] = (home_slug, home_id)
        if away_name and away_slug and away_id:
            teams[away_name] = (away_slug, away_id)

    return teams


# ── Step 2: Fetch one team's recent results ───────────────────────────────────

def fetch_team_results(
    team_name: str,
    slug: str,
    team_id: str,
    n: int = 5,
    before_date: str = _WC_START,
) -> list[dict]:
    """
    Return the last `n` completed matches for a team before `before_date`.

    Matches are returned newest-first.  Friendlies may be included —
    Flashscore does not label competition type in the team results feed.
    Deduplication is done by match_id (AA field) to handle duplicate records.

    Each dict:
      team, date, competition_id, opponent, home_away,
      goals_for, goals_against, result, match_id
    """
    url = f"https://www.flashscore.com/team/{slug}/{team_id}/results/"
    body = _get(url)

    seen_ids: set[str] = set()
    matches: list[dict] = []

    for raw in body.split("~AA÷")[1:]:
        f = _parse_record("AA÷" + raw)

        match_id = f.get("AA", "")
        if not match_id or match_id in seen_ids:
            continue
        seen_ids.add(match_id)

        # Only completed matches (status 3 = full time on team pages)
        if f.get("AB", "") != "3":
            continue

        ts_raw = f.get("AD", "")
        date_str = ""
        if ts_raw and ts_raw.isdigit():
            try:
                date_str = datetime.datetime.utcfromtimestamp(
                    int(ts_raw)
                ).strftime("%Y-%m-%d")
            except (ValueError, OSError):
                pass

        if not date_str or date_str >= before_date:
            continue

        home_name = f.get("CX") or f.get("AE", "")
        away_name = f.get("AF") or f.get("FK", "")

        # Goals: AG = home team goals, AH = away team goals
        try:
            home_goals = int(f.get("AG", ""))
            away_goals = int(f.get("AH", ""))
        except (ValueError, TypeError):
            continue  # skip if scores aren't numeric (not fully finished)

        if home_name == team_name:
            goals_for     = home_goals
            goals_against = away_goals
            opponent      = away_name
            home_away     = "H"
        else:
            goals_for     = away_goals
            goals_against = home_goals
            opponent      = home_name
            home_away     = "A"

        if goals_for > goals_against:
            result = "W"
        elif goals_for < goals_against:
            result = "L"
        else:
            result = "D"

        matches.append({
            "team":           team_name,
            "date":           date_str,
            "competition_id": f.get("JA", ""),
            "opponent":       opponent,
            "home_away":      home_away,
            "goals_for":      goals_for,
            "goals_against":  goals_against,
            "result":         result,
            "match_id":       match_id,
        })

        if len(matches) >= n:
            break

    return matches


# ── Step 3: Fetch form data for all WC teams ─────────────────────────────────

def fetch_all_teams_form(
    n_matches: int = 5,
    delay: float = 1.5,
    before_date: str = _WC_START,
    progress_callback=None,
) -> tuple[pd.DataFrame, list[str]]:
    """
    Fetch pre-tournament form for all 48 WC teams.

    Returns:
      (form_df, errors)
      form_df: DataFrame with FORM_COLUMNS
      errors:  list of team names that failed

    progress_callback: optional callable(done, total, team_name) for UI updates.
    """
    team_ids = extract_wc_team_ids()
    if not team_ids:
        raise ScrapeError(
            "Could not read team IDs from the WC scoreboard. "
            "Check internet connection."
        )

    all_rows: list[dict] = []
    errors: list[str] = []
    total = len(team_ids)

    for i, (team_name, (slug, team_id)) in enumerate(sorted(team_ids.items())):
        if progress_callback:
            progress_callback(i, total, team_name)
        try:
            rows = fetch_team_results(
                team_name, slug, team_id, n=n_matches, before_date=before_date
            )
            all_rows.extend(rows)
        except ScrapeError as e:
            errors.append(f"{team_name}: {e}")
        if i < total - 1:
            time.sleep(delay)

    df = pd.DataFrame(all_rows, columns=FORM_COLUMNS) if all_rows else pd.DataFrame(
        columns=FORM_COLUMNS
    )
    return df, errors


# ── Model integration: compute team form stats ────────────────────────────────

def get_form_stats(form_df: "pd.DataFrame | None") -> dict:
    """
    Compute team-level statistics from pre-tournament form data.

    Returns:
      {
        "team_gpg":          {team: goals_per_game},
        "team_concede_gpg":  {team: conceded_per_game},
        "team_games":        {team: n_matches},
      }

    These values can update the model's prior attack/defense estimates
    before any tournament results are available.
    """
    if form_df is None or form_df.empty:
        return {"team_gpg": {}, "team_concede_gpg": {}, "team_games": {}}

    team_gpg: dict[str, float]         = {}
    team_concede_gpg: dict[str, float] = {}
    team_games: dict[str, int]         = {}

    df = form_df.copy()
    df["goals_for"]     = pd.to_numeric(df["goals_for"],     errors="coerce").fillna(0)
    df["goals_against"] = pd.to_numeric(df["goals_against"], errors="coerce").fillna(0)

    for team, grp in df.groupby("team"):
        n    = len(grp)
        gf   = grp["goals_for"].sum()
        ga   = grp["goals_against"].sum()
        team_gpg[team]         = round(gf / n, 3) if n else 0.0
        team_concede_gpg[team] = round(ga / n, 3) if n else 0.0
        team_games[team]       = n

    return {
        "team_gpg":         team_gpg,
        "team_concede_gpg": team_concede_gpg,
        "team_games":       team_games,
    }


# ── Load/save helpers ─────────────────────────────────────────────────────────

_DATA_DIR = Path(__file__).parent.parent.parent / "Data"


def load_team_form() -> pd.DataFrame:
    path = _DATA_DIR / "team_form.csv"
    if path.exists():
        try:
            return pd.read_csv(path, dtype=str)
        except Exception:
            pass
    return pd.DataFrame(columns=FORM_COLUMNS)


def save_team_form(df: pd.DataFrame) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(_DATA_DIR / "team_form.csv", index=False)
