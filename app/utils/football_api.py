"""
football-data.org API client for WC 2026.

Free tier: 10 requests/minute.
Register (free, 2 min) at https://www.football-data.org/client/register
Competition: FIFA World Cup, code "WC", competition ID 2000.
"""
from __future__ import annotations
import json
import urllib.request
import urllib.error
from pathlib import Path

import pandas as pd

API_BASE    = "https://api.football-data.org/v4"
WC_CODE     = "WC"
WC_SEASON   = "2026"
CONFIG_PATH = Path(__file__).parent.parent.parent / "Data" / "api_config.json"

# ── Team name normalisation ───────────────────────────────────────────────────
# football-data.org uses slightly different names in some cases.
# Keys = what the API returns; values = what our CSVs use.
# Add entries here when a mismatch is discovered after first sync.
_API_TO_LOCAL: dict[str, str] = {
    "México":                        "Mexico",
    "Republic of Korea":             "Korea Republic",
    "Cape Verde":                    "Cabo Verde",
    "Iran":                          "IR Iran",
    "Democratic Republic Congo":     "Congo DR",
    "DR Congo":                      "Congo DR",
    "Ivory Coast":                   "Côte d'Ivoire",
    "Cote d'Ivoire":                 "Côte d'Ivoire",
    "Turkey":                        "Türkiye",
    "Bosnia Herzegovina":            "Bosnia and Herzegovina",
}


def _norm_team(api_name: str) -> str:
    return _API_TO_LOCAL.get(api_name, api_name)


# ── Config / key storage ──────────────────────────────────────────────────────

def load_api_key() -> str:
    """Return stored API key, or empty string if not configured."""
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                return json.load(f).get("football_data_api_key", "")
        except Exception:
            pass
    return ""


def save_api_key(key: str) -> None:
    """Save API key to local config file (not committed to git)."""
    cfg: dict = {}
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                cfg = json.load(f)
        except Exception:
            pass
    cfg["football_data_api_key"] = key.strip()
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


def save_last_sync(timestamp: str) -> None:
    cfg: dict = {}
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                cfg = json.load(f)
        except Exception:
            pass
    cfg["last_sync"] = timestamp
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


def load_last_sync() -> str:
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                return json.load(f).get("last_sync", "")
        except Exception:
            pass
    return ""


# ── HTTP helpers ──────────────────────────────────────────────────────────────

class APIError(Exception):
    """Raised for non-200 responses with a human-readable message."""


def _get(path: str, api_key: str) -> dict:
    url = f"{API_BASE}/{path}"
    req = urllib.request.Request(url, headers={"X-Auth-Token": api_key})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise APIError("Invalid API key — double-check your key at football-data.org.")
        if e.code == 403:
            raise APIError(
                "Your plan doesn't include this competition. "
                "The free tier covers the World Cup — make sure you are using a free-tier key."
            )
        if e.code == 429:
            raise APIError("Rate limit hit (10 req/min on free tier). Wait 60 seconds and try again.")
        if e.code == 404:
            raise APIError(
                "WC 2026 data not found. The API may not have opened season 2026 yet — "
                "try again once the tournament is underway."
            )
        raise APIError(f"API error {e.code}: {e.reason}")
    except urllib.error.URLError as e:
        raise APIError(f"Could not reach football-data.org — check your internet connection. ({e.reason})")


# ── API calls ─────────────────────────────────────────────────────────────────

def test_connection(api_key: str) -> tuple[bool, str]:
    """
    Verify the key works and WC 2026 is accessible.
    Returns (ok, message).
    """
    if not api_key:
        return False, "No API key entered."
    try:
        data = _get(f"competitions/{WC_CODE}", api_key)
        name = data.get("name", WC_CODE)
        return True, f"Connected ✓  —  {name}"
    except APIError as e:
        return False, str(e)


def fetch_wc_matches(api_key: str) -> list[dict]:
    """
    Fetch all WC 2026 matches.
    Raises APIError on failure.
    """
    data = _get(f"competitions/{WC_CODE}/matches?season={WC_SEASON}", api_key)
    return data.get("matches", [])


# ── Data transformation ───────────────────────────────────────────────────────

_PLAYED_STATUSES = {"FINISHED", "IN_PLAY", "PAUSED"}


def _parse_goalscorers(goals: list[dict]) -> str:
    """Convert API goals array to comma-separated scorer string."""
    names = []
    for g in goals:
        if g.get("type") == "OWN_GOAL":
            continue
        name = g.get("scorer", {}).get("name") or g.get("scorer", {}).get("shortName", "")
        minute = g.get("minute", "")
        if name:
            names.append(f"{name} {minute}'" if minute else name)
    return ", ".join(names)


def build_results_df(matches: list[dict]) -> pd.DataFrame:
    """
    Convert the API match list to a DataFrame matching our results.csv schema:
    match_id | date | home_team | away_team | home_score | away_score | goalscorers

    Only includes matches that have been played (status FINISHED / IN_PLAY).
    Placeholder knockout matches ("Winner 73 vs Winner 76") are excluded.
    """
    rows = []
    for m in matches:
        status = m.get("status", "")
        if status not in _PLAYED_STATUSES:
            continue

        home = _norm_team(m.get("homeTeam", {}).get("name", ""))
        away = _norm_team(m.get("awayTeam", {}).get("name", ""))

        # Skip placeholder knockout rows (names contain "Winner" / TBD)
        if not home or not away or home.startswith("Winner") or away.startswith("Winner"):
            continue

        score   = m.get("score", {})
        ft      = score.get("fullTime", {})
        hs      = ft.get("home")
        as_     = ft.get("away")
        goals   = m.get("goals", [])
        date    = (m.get("utcDate") or "")[:10]  # YYYY-MM-DD

        rows.append({
            "match_id":    m.get("id", ""),
            "date":        date,
            "home_team":   home,
            "away_team":   away,
            "home_score":  "" if hs is None else str(hs),
            "away_score":  "" if as_ is None else str(as_),
            "goalscorers": _parse_goalscorers(goals),
        })

    return pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=["match_id", "date", "home_team", "away_team",
                 "home_score", "away_score", "goalscorers"]
    )


def merge_into_results(
    existing: pd.DataFrame,
    fetched: pd.DataFrame,
) -> tuple[pd.DataFrame, list[str]]:
    """
    Merge newly fetched results into the existing results DataFrame.

    Matching is by (home_team, away_team) pair — robust even if our internal
    match_id differs from the API's match ID.

    Returns:
      merged  – updated DataFrame (same rows as existing, scores filled in)
      changes – human-readable list of what was updated ("Germany 3-0 Curaçao")
    """
    if fetched.empty:
        return existing, []

    # Build lookup: (home_norm, away_norm) → fetched row
    fetched_lookup: dict[tuple[str, str], dict] = {}
    for _, r in fetched.iterrows():
        key = (str(r["home_team"]).strip().lower(), str(r["away_team"]).strip().lower())
        fetched_lookup[key] = r.to_dict()

    changes: list[str] = []
    updated_rows = []

    for _, row in existing.iterrows():
        h = str(row.get("home_team", "")).strip()
        a = str(row.get("away_team", "")).strip()
        key = (h.lower(), a.lower())

        if key in fetched_lookup:
            new = fetched_lookup[key]
            hs_old = str(row.get("home_score", "")).strip()
            as_old = str(row.get("away_score", "")).strip()
            hs_new = str(new.get("home_score", "")).strip()
            as_new = str(new.get("away_score", "")).strip()

            if hs_new and as_new:
                # Score updated
                if hs_old != hs_new or as_old != as_new:
                    changes.append(f"{h} {hs_new}–{as_new} {a}")
                merged_row = {
                    **row.to_dict(),
                    "home_score":  hs_new,
                    "away_score":  as_new,
                    "goalscorers": new.get("goalscorers", row.get("goalscorers", "")),
                }
                updated_rows.append(merged_row)
                continue

        updated_rows.append(row.to_dict())

    return pd.DataFrame(updated_rows), changes
