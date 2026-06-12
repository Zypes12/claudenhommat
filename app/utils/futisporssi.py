"""
futisporssi.fi price scraper for WC 2026.

Player prices on futisporssi.fi change after each match day based on
performance, scoring, and selection percentage.

Strategy:
  1. Discover player IDs by scraping the public top-performers page
     (/futis/pelaajat) and each team's featured player section
     (/futis/joukkueet/{slug}/pelaajat).  Discovered IDs are persisted in
     Data/fp_player_ids.json so they accumulate across sync runs.
  2. For each known player ID, fetch the individual price page and parse:
     current price, original price, and % change.
  3. Match scraped names back to players.csv rows (fuzzy on normalised name)
     and update the "value" column.

No login required — all price pages are publicly accessible.
"""
from __future__ import annotations
import json
import re
import time
import unicodedata
import urllib.request
import urllib.error
from pathlib import Path

import pandas as pd

# ── Constants ─────────────────────────────────────────────────────────────────

BASE_URL        = "https://futisporssi.fi"
PLAYER_LIST_URL = BASE_URL + "/futis/pelaajat"
TEAM_SQUAD_URL  = BASE_URL + "/futis/joukkueet/{slug}/pelaajat"
PLAYER_PAGE_URL = BASE_URL + "/futis/pelaajat/pelaaja/{slug}"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "fi-FI,fi;q=0.9,en;q=0.8",
}

# Finnish team name slugs as they appear in futisporssi.fi URLs
FP_TEAM_SLUGS = [
    "algeria", "argentiina", "australia", "belgia", "bosnia-hertsegovina",
    "brasilia", "curacao", "ecuador", "egypti", "englanti", "espanja",
    "etela-afrikka", "etela-korea", "ghana", "haiti", "hollanti", "irak",
    "iran", "itavalta", "japani", "jordania", "kanada", "kap-verde",
    "kolumbia", "kongon-demokraattinen-tasavalta", "kroatia", "marokko",
    "meksiko", "norja", "norsunluurannikko", "panama", "paraguay",
    "portugali", "qatar", "ranska", "ruotsi", "saksa", "saudi-arabia",
    "senegal", "skotlanti", "sveitsi", "tsekki", "tunisia", "turkki",
    "uruguay", "usa", "uusi-seelanti", "uzbekistan",
]

_ID_STORE = Path(__file__).parent.parent.parent / "Data" / "fp_player_ids.json"


class FPScrapeError(Exception):
    pass


# ── HTTP ──────────────────────────────────────────────────────────────────────

def _get(url: str, timeout: int = 12) -> str:
    req = urllib.request.Request(url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        raise FPScrapeError(f"HTTP {e.code}: {url}")
    except urllib.error.URLError as e:
        raise FPScrapeError(f"Network error: {e.reason}")


# ── Player ID store ───────────────────────────────────────────────────────────

def _load_id_store() -> dict[str, str]:
    """Load persisted {slug → fp_name} mapping from disk."""
    if _ID_STORE.exists():
        try:
            return json.loads(_ID_STORE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_id_store(store: dict[str, str]) -> None:
    _ID_STORE.parent.mkdir(parents=True, exist_ok=True)
    _ID_STORE.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")


def _extract_player_slugs(body: str) -> list[str]:
    """Return all unique player slugs found in a page's HTML."""
    slugs = re.findall(r"/futis/pelaajat/pelaaja/([a-z0-9\-]+)", body)
    return list(dict.fromkeys(slugs))   # deduplicate, preserve order


# ── Price parsing ─────────────────────────────────────────────────────────────

def _parse_price(body: str) -> dict | None:
    """
    Parse a player's price page.

    Returns:
      name          – player name as shown on fp (e.g. "Hwang In-beom")
      current_value – current price in euros (int)
      original_value– starting price in euros (int)
      change_pct    – percentage change (float, positive = risen)
    Returns None if the page doesn't look like a valid player page.
    """
    # Player name — first non-empty h2 on the page
    name = ""
    for h2_content in re.findall(r"<h2[^>]*>(.*?)</h2>", body, re.DOTALL):
        candidate = re.sub(r"<[^>]+>", "", h2_content).strip()
        if candidate and len(candidate) >= 2:
            # Skip generic section headings
            if candidate.lower() not in ("edellinen ottelu", "pelipäivän pisteet",
                                         "statistiikka", "uutiset"):
                name = candidate
                break
    if not name:
        return None

    # Current price: "Arvo ... <strong>NNN NNN &euro; ..."
    arvo_m = re.search(
        r"Arvo\s*<strong>\s*([\d\s]+)\s*&euro;",
        body,
        re.DOTALL | re.IGNORECASE,
    )
    if not arvo_m:
        return None
    current_value = int(re.sub(r"\s", "", arvo_m.group(1)))

    # Original price + % — from title attribute on the span inside the price block
    orig_m = re.search(
        r'title="Alkuper[^"]*?(\d[\d\s]+)&euro;"[^>]*>\(([^)]+)\)',
        body,
    )
    if orig_m:
        original_value = int(re.sub(r"\s", "", orig_m.group(1)))
        pct_str = orig_m.group(2).strip().replace(",", ".").replace("%", "").strip()
        try:
            change_pct = float(pct_str)
        except ValueError:
            change_pct = 0.0
    else:
        original_value = current_value
        change_pct = 0.0

    return {
        "name":           name,
        "current_value":  current_value,
        "original_value": original_value,
        "change_pct":     change_pct,
    }


# ── Name normalisation for matching ──────────────────────────────────────────

def _norm_name(s: str) -> str:
    """Lowercase, strip accents, collapse spaces."""
    nfkd = unicodedata.normalize("NFKD", str(s))
    ascii_s = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", ascii_s).lower().strip()


def _name_tokens(s: str) -> set[str]:
    return set(_norm_name(s).split())


def _match_score(fp_name: str, csv_name: str) -> float:
    """
    Score how well a futisporssi player name matches a players.csv name.
    Returns 0.0–1.0 (1.0 = perfect).
    """
    fp_t   = _name_tokens(fp_name)
    csv_t  = _name_tokens(csv_name)
    if not fp_t or not csv_t:
        return 0.0
    if fp_t == csv_t:
        return 1.0
    overlap = fp_t & csv_t
    if not overlap:
        return 0.0
    # Jaccard-like: intersection / union
    score = len(overlap) / len(fp_t | csv_t)
    return score


# ── Discover player IDs ───────────────────────────────────────────────────────

def discover_player_ids(
    delay: float = 1.2,
    progress_callback=None,
) -> dict[str, str]:
    """
    Scrape the top-performers page and all 48 team pages to collect
    player slugs.  Returns the updated {slug → page-title} store.
    """
    store = _load_id_store()
    new_found = 0

    pages_to_scrape = [PLAYER_LIST_URL] + [
        TEAM_SQUAD_URL.format(slug=s) for s in FP_TEAM_SLUGS
    ]
    total = len(pages_to_scrape)

    for i, url in enumerate(pages_to_scrape):
        if progress_callback:
            progress_callback(i, total, url.split("/")[-1])
        try:
            body = _get(url)
        except FPScrapeError:
            if i < total - 1:
                time.sleep(delay)
            continue

        for slug in _extract_player_slugs(body):
            if slug not in store:
                store[slug] = ""   # placeholder; name filled on price fetch
                new_found += 1

        if i < total - 1:
            time.sleep(delay)

    _save_id_store(store)
    return store


# ── Fetch prices for all known player IDs ─────────────────────────────────────

def fetch_all_prices(
    store: dict[str, str] | None = None,
    delay: float = 1.2,
    progress_callback=None,
) -> list[dict]:
    """
    Fetch price pages for every slug in the store.

    Returns a list of parsed price dicts with an added "slug" key:
      slug, name, current_value, original_value, change_pct
    """
    # Always load the full store from disk so we never overwrite other entries.
    full_store = _load_id_store()
    if store is not None:
        # Merge caller-provided store into the full on-disk store.
        full_store.update(store)

    results = []
    slugs = list(full_store.keys())
    total = len(slugs)

    for i, slug in enumerate(slugs):
        if progress_callback:
            progress_callback(i, total, slug)
        url = PLAYER_PAGE_URL.format(slug=slug)
        try:
            body = _get(url)
        except FPScrapeError:
            if i < total - 1:
                time.sleep(delay)
            continue

        parsed = _parse_price(body)
        if parsed:
            parsed["slug"] = slug
            results.append(parsed)
            full_store[slug] = parsed["name"]

        if i < total - 1:
            time.sleep(delay)

    _save_id_store(full_store)
    return results


# ── Apply prices to players.csv ───────────────────────────────────────────────

def apply_prices(
    players: pd.DataFrame,
    price_data: list[dict],
    match_threshold: float = 0.5,
) -> tuple[pd.DataFrame, list[str]]:
    """
    Match scraped prices to players.csv rows and update the "value" column.

    Returns (updated_players_df, list_of_change_strings).
    Uses fuzzy name matching — at least 50% token overlap required.
    """
    if players.empty or not price_data:
        return players, []

    updated = players.copy()
    changes: list[str] = []

    for price in price_data:
        fp_name = price["name"]
        best_row_idx = None
        best_score   = 0.0

        for idx, row in updated.iterrows():
            csv_name = str(row.get("name", ""))
            score = _match_score(fp_name, csv_name)
            if score > best_score:
                best_score   = score
                best_row_idx = idx

        if best_row_idx is None or best_score < match_threshold:
            continue

        new_val = f"{price['current_value']:,} €".replace(",", " ")
        old_val = str(updated.at[best_row_idx, "value"]).strip()

        # Extract numeric part of old value for comparison (ignore encoding differences)
        old_num_str = re.sub(r"[^\d]", "", old_val)
        new_num = price["current_value"]
        old_num = int(old_num_str) if old_num_str else None

        updated.at[best_row_idx, "value"] = new_val

        # Only report as a change if the numeric value actually changed
        if old_num != new_num:
            player_name = str(updated.at[best_row_idx, "name"])
            pct = price["change_pct"]
            sign = "▲" if pct > 0 else ("▼" if pct < 0 else "=")
            changes.append(
                f"{player_name}: {old_val} → {new_val} ({sign}{abs(pct):.0f}%)"
            )

    return updated, changes


# ── Main sync entry ───────────────────────────────────────────────────────────

def sync_prices(
    players: pd.DataFrame,
    delay: float = 1.2,
    discover_new: bool = True,
    discover_delay: float = 0.8,
    progress_callback=None,
) -> dict:
    """
    Full price sync:
      1. Optionally scrape pages to discover new player IDs.
      2. Fetch price pages for all known players.
      3. Apply updated prices to players DataFrame.

    Returns:
      players        – updated DataFrame
      changes        – list of change strings
      prices_fetched – int
      known_slugs    – int (total in store after discovery)
    """
    store = _load_id_store()

    if discover_new:
        store = discover_player_ids(delay=discover_delay, progress_callback=None)

    price_data = fetch_all_prices(
        store=store,
        delay=delay,
        progress_callback=progress_callback,
    )

    updated_players, changes = apply_prices(players, price_data)

    return {
        "players":        updated_players,
        "changes":        changes,
        "prices_fetched": len(price_data),
        "known_slugs":    len(store),
    }
