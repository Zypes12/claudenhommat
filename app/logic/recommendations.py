"""
Recommendation logic for Futispörssi WC2026.

Scoring rules (from futisporssi.fi/ohjeet):
  GK:  goal +9, assist +6, win +3, draw +1, loss -2, clean_sheet +3, saved_pen +4
       saves: 1-2→+1, 3-4→+2, 5-6→+3, 7-8→+4, 9+→+5
  DEF: goal +7, assist +4, win +2, draw +1, loss -1, clean_sheet +2
       goals_against: 1-2→-1, 3-4→-2, 5-6→-3, 7-8→-4
  MID: goal +5, assist +3, win +1, clean_sheet +1
  FWD: goal +4, assist +2
  ALL: appearance +1, 60min +1, yellow -1, red -4, missed_pen -2, own_goal -1
       shots_on_target: 1-2→+1, 3-4→+2, 5-6→+3, 7-8→+4, 9+→+5
       victory_goal +2, equalizer +1
  Captain: ×1.3 (positive rounds up, negative rounds down)

Strategy rules baked into scoring (Finnish Futispörssi community guidelines):
  1. Defensive MIDs penalised: no set-piece/penalty role → 30% lower goal rate.
  2. Goal rates scale with opponent weakness (FIFA rank) AND team attacking form.
  3. Knockout fixtures: CS probability boosted 25%; goal factor reduced 15%.
  4. Transfer rule: never recommend transferring out a player who plays within 2 days.
  5. Transfer focus: FWD/attacking MID upgrades prioritised over DEF/GK swaps.
  6. Transfer up in value: small preference for expensive in-players (budget maximisation).
"""
from __future__ import annotations
import datetime
import math
import re
import unicodedata
import pandas as pd


# ── Constants ──────────────────────────────────────────────────────────────────

POSITION_POINTS: dict[str, dict] = {
    "GK":  {"goal": 9, "assist": 6, "win": 3, "draw": 1, "loss": -2, "clean_sheet": 3},
    "DEF": {"goal": 7, "assist": 4, "win": 2, "draw": 1, "loss": -1, "clean_sheet": 2},
    "MID": {"goal": 5, "assist": 3, "win": 1, "draw": 0, "loss":  0, "clean_sheet": 1},
    "FWD": {"goal": 4, "assist": 2, "win": 0, "draw": 0, "loss":  0, "clean_sheet": 0},
}
CAPTAIN_MULTIPLIER = 1.3
BUDGET = 3_800_000
SQUAD_SIZE = 11
MAX_TRANSFERS = 35
VALID_FORMATIONS = ["4-4-2", "4-3-3", "4-5-1", "3-5-2", "3-4-3", "5-3-2", "5-4-1"]

POS_COLORS = {"GK": "#f59e0b", "DEF": "#22c55e", "MID": "#3b82f6", "FWD": "#ef4444"}

# WC 2026 host nations — treated as ~7 FIFA ranks stronger than their actual ranking
# because playing in front of a home crowd at familiar venues.
HOST_NATIONS: frozenset[str] = frozenset({"USA", "United States", "Canada", "Mexico"})
HOME_RANK_BOOST: float = 7.0

# Goals per game at the position level vs an average opponent (FIFA rank ~50).
BASE_GOAL_RATE = {"GK": 0.01, "DEF": 0.04, "MID": 0.08, "FWD": 0.25}

# Assists per game — separate from goal rate because playmakers (MIDs) create
# chances for others; strikers mostly finish, not create.
BASE_ASSIST_RATE = {"GK": 0.005, "DEF": 0.025, "MID": 0.10, "FWD": 0.06}

# Shot-to-goal conversion rate by position — used to estimate shots on target
# from expected goals (SoT = exp_goals / conversion_rate).
SHOT_CONVERSION = {"GK": 0.0, "DEF": 0.07, "MID": 0.12, "FWD": 0.18}

# Baseline shots on target per game against a rank-50 opponent in WC matches.
# WC 2022 average was ~3.4 SoT per team per game.
AVG_SOT_AGAINST = 3.4

# Confederation qualifying difficulty.
# A team's raw qualifying GPG is multiplied by this before computing attack_factor,
# so that e.g. Egypt's 3.2 gpg in AFCON (CAF=0.60) ≈ a UEFA team scoring 1.9 gpg.
# Ensures European form is the baseline — not inflated by weak-opposition qualifying.
CONFEDERATION_STRENGTH: dict[str, float] = {
    "UEFA":     1.00,   # European qualifying: hardest — rank 10-70 opposition
    "CONMEBOL": 0.85,   # South American: rank 5-60 opposition
    "AFC":      0.72,   # Asian: rank 50-150, competitive within region
    "CONCACAF": 0.65,   # North/Central American: moderate opposition
    "CAF":      0.58,   # African: opposition often rank 60-150
    "OFC":      0.40,   # Oceania: weakest confederation
}

TEAM_CONFEDERATION: dict[str, str] = {
    # UEFA
    "Czechia": "UEFA", "Switzerland": "UEFA", "Scotland": "UEFA",
    "Bosnia and Herzegovina": "UEFA", "Türkiye": "UEFA", "Turkey": "UEFA",
    "Germany": "UEFA", "Sweden": "UEFA", "Netherlands": "UEFA",
    "Belgium": "UEFA", "Spain": "UEFA", "Norway": "UEFA",
    "France": "UEFA", "Austria": "UEFA", "Portugal": "UEFA",
    "England": "UEFA", "Croatia": "UEFA", "Denmark": "UEFA",
    "Serbia": "UEFA", "Poland": "UEFA", "Ukraine": "UEFA",
    # CONMEBOL
    "Brazil": "CONMEBOL", "Paraguay": "CONMEBOL", "Ecuador": "CONMEBOL",
    "Uruguay": "CONMEBOL", "Argentina": "CONMEBOL", "Colombia": "CONMEBOL",
    "Chile": "CONMEBOL", "Peru": "CONMEBOL",
    # CAF
    "South Africa": "CAF", "Morocco": "CAF", "Côte d'Ivoire": "CAF",
    "Ivory Coast": "CAF", "Tunisia": "CAF", "Egypt": "CAF",
    "Cabo Verde": "CAF", "Cape Verde": "CAF", "Senegal": "CAF",
    "Algeria": "CAF", "Congo DR": "CAF", "Ghana": "CAF",
    "Nigeria": "CAF", "Cameroon": "CAF", "Mali": "CAF",
    # CONCACAF
    "Mexico": "CONCACAF", "Canada": "CONCACAF", "Haiti": "CONCACAF",
    "USA": "CONCACAF", "United States": "CONCACAF", "Curaçao": "CONCACAF",
    "Curacao": "CONCACAF", "Panama": "CONCACAF", "Costa Rica": "CONCACAF",
    "Jamaica": "CONCACAF", "Honduras": "CONCACAF",
    # AFC
    "Korea Republic": "AFC", "South Korea": "AFC", "Qatar": "AFC",
    "Australia": "AFC", "Japan": "AFC", "IR Iran": "AFC", "Iran": "AFC",
    "Saudi Arabia": "AFC", "Iraq": "AFC", "Uzbekistan": "AFC",
    "Jordan": "AFC", "China": "AFC", "India": "AFC",
    # OFC
    "New Zealand": "OFC",
}


# ── Value parsing ──────────────────────────────────────────────────────────────

def parse_value(val) -> float:
    """Convert '275 000 €', '275�000', etc. → 275000.0 by keeping only digits."""
    digits = re.sub(r"[^\d]", "", str(val))
    return float(digits) if digits else 0.0


def display_name(stored_name: str) -> str:
    """
    players.csv stores names as 'LastName FirstName'.
    Returns 'FirstName LastName' for display.
    Single-word names are returned unchanged.
    """
    parts = str(stored_name).strip().split()
    if len(parts) <= 1:
        return stored_name
    # Last token = first name; everything before = surname
    return parts[-1] + " " + " ".join(parts[:-1])


# ── Difficulty labels ──────────────────────────────────────────────────────────

def difficulty_label(avg_opp_rank: float) -> tuple[str, str]:
    """Returns (label, hex_color) based on average opponent FIFA ranking."""
    if avg_opp_rank >= 60:
        return "Easy", "#22c55e"
    elif avg_opp_rank >= 30:
        return "Medium", "#f59e0b"
    else:
        return "Hard", "#ef4444"


# ── Team enrichment ────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    """Lowercase, strip accents and encoding-error replacements for fuzzy matching."""
    nfkd = unicodedata.normalize("NFKD", str(s))
    # Drop combining marks AND replacement characters (U+FFFD from encoding errors)
    ascii_s = "".join(
        c for c in nfkd
        if not unicodedata.combining(c) and c != "�"
    )
    return ascii_s.lower().strip()


def _enrich_with_team(players: pd.DataFrame, lineups: pd.DataFrame) -> pd.DataFrame:
    """
    Add 'team' column to players by joining with lineups.csv.
    Uses partial name matching (lineup names are often last-name only).
    """
    df = players.copy()
    if lineups.empty or "player_name" not in lineups.columns or "team" not in lineups.columns:
        df["team"] = ""
        return df

    # Build lookup: normalised_lineup_name → team
    lineup_map: dict[str, str] = {}
    for _, row in lineups.iterrows():
        key = _norm(str(row["player_name"]))
        lineup_map[key] = str(row["team"]).strip()

    # Find last names that appear for more than one team — skip last-name-only
    # matching for these to avoid cross-team collisions (e.g. Mendes → Portugal
    # when the player is Ryan Mendes from Cape Verde).
    last_to_teams: dict[str, set] = {}
    for key, team in lineup_map.items():
        last = key.split()[0] if key.split() else key
        last_to_teams.setdefault(last, set()).add(team)
    ambiguous_last: set[str] = {k for k, v in last_to_teams.items() if len(v) > 1}

    # Build secondary lookup for "Lastname I." format (e.g. "Quinones J." → Mexico)
    # Key = "lastname_initial" to resolve "Quiñones Julián" → "quinones_j" → Mexico
    initial_map: dict[str, str] = {}
    for key, team in lineup_map.items():
        key_parts = key.split()
        if len(key_parts) == 2 and key_parts[1].endswith(".") and len(key_parts[1]) == 2:
            init_key = key_parts[0] + "_" + key_parts[1][0]
            if init_key not in initial_map:
                initial_map[init_key] = team

    def find_team(player_name: str) -> str:
        norm_player = _norm(player_name)
        parts = norm_player.split()

        # 1. Exact full-name match (most reliable)
        if norm_player in lineup_map:
            return lineup_map[norm_player]

        # 2. Last-name-only — skip if the surname is shared across multiple teams
        if parts and parts[0] in lineup_map and parts[0] not in ambiguous_last:
            return lineup_map[parts[0]]

        # 3. Compound last name (e.g. "De Bruyne", "Van Dijk", "El Kaabi")
        if len(parts) >= 2:
            compound = parts[0] + " " + parts[1]
            if compound in lineup_map:
                return lineup_map[compound]

        # 4. Lastname + first-initial match (lineup uses "Quinones J." format)
        if len(parts) >= 2:
            init_key = parts[0] + "_" + parts[1][0]
            if init_key in initial_map:
                return initial_map[init_key]

        # 5. If players.csv already has a team column, fall through — handled in caller
        return ""

    lineup_resolved = df["name"].astype(str).apply(find_team)

    # If players.csv already carries a "team" column, use it as a fallback for
    # players not found in lineups (covers bench/rotation players not in the XI).
    if "team" in df.columns:
        existing = df["team"].astype(str).str.strip()
        df["team"] = lineup_resolved.where(lineup_resolved != "", existing)
    else:
        df["team"] = lineup_resolved

    return df


# ── Fixture / ranking helpers ──────────────────────────────────────────────────

def get_team_ranking(team: str, groups: pd.DataFrame) -> float:
    if groups.empty or not team:
        return 50.0
    row = groups[groups["team"].astype(str).str.strip() == team.strip()]
    if row.empty:
        return 50.0
    try:
        return float(row.iloc[0]["fifa_ranking"])
    except (ValueError, TypeError):
        return 50.0


def _expected_tier_pts(exp_count: float) -> float:
    """
    Expected points from a tiered bonus (1-2→+1, 3-4→+2, 5-6→+3, 7-8→+4, 9+→+5).

    Used for both GK saves and shots-on-target bonuses. Models the count as a
    Poisson random variable and computes the expectation of the tier value.
    Uses the incremental PMF formula to avoid factorial overflow.
    """
    if exp_count <= 0:
        return 0.0
    pts = 0.0
    p_k = math.exp(-exp_count)  # P(k=0); we start from k=1 below
    for k in range(1, 25):
        p_k = p_k * exp_count / k          # P(k) = P(k-1) × λ/k
        tier = min(5, (k - 1) // 2 + 1)   # 1-2→1, 3-4→2, 5-6→3, 7-8→4, 9+→5
        pts += p_k * tier
        if p_k < 1e-9:
            break
    return pts


def _result_probs(team_rank: float, opp_rank: float) -> tuple[float, float, float]:
    """
    Return (win, draw, loss) probabilities that always sum to 1.0.

    Draw probability decreases as the rank gap widens — closely matched teams
    draw far more often than mismatches.  Win/loss take the remaining probability
    proportionally from the logistic win-rate estimate.
    """
    rank_diff = opp_rank - team_rank            # positive = weaker opponent
    p_win_base = 1 / (1 + math.exp(-rank_diff * 0.04))
    # Draw more likely in close fixtures; decay exponentially with rank gap
    p_draw = max(0.08, 0.28 * math.exp(-0.003 * abs(rank_diff)))
    remaining = 1.0 - p_draw
    p_win  = max(0.03, p_win_base  * remaining)
    p_loss = max(0.03, (1 - p_win_base) * remaining)
    total = p_win + p_draw + p_loss
    return p_win / total, p_draw / total, p_loss / total


def fixture_difficulty(
    team: str, fixtures: pd.DataFrame, groups: pd.DataFrame, next_n: int = 3
) -> float:
    """Average opponent FIFA ranking across next N unplayed fixtures. Higher = easier."""
    if fixtures.empty or groups.empty or not team:
        return 50.0
    unplayed = fixtures
    if "home_score" in fixtures.columns:
        unplayed = fixtures[
            fixtures["home_score"].isna() | (fixtures["home_score"].astype(str).str.strip() == "")
        ]
    team_fx = unplayed[
        (unplayed["home_team"].astype(str).str.strip() == team.strip()) |
        (unplayed["away_team"].astype(str).str.strip() == team.strip())
    ].head(next_n)
    if team_fx.empty:
        return 50.0
    scores = []
    for _, row in team_fx.iterrows():
        opp = row["away_team"] if str(row["home_team"]).strip() == team.strip() else row["home_team"]
        scores.append(get_team_ranking(str(opp).strip(), groups))
    return round(sum(scores) / len(scores), 1)


# ── Attacking form helpers ─────────────────────────────────────────────────────

def _opp_goal_factor(opp_rank: float) -> float:
    """
    Scoring ease multiplier based on opponent FIFA rank.
    Rank 50 (average) → 1.0; rank 10 (elite) → 0.2; rank 100 (weak) → 2.0.
    """
    return min(2.0, max(0.2, opp_rank / 50.0))


def get_team_attack_rate(team: str, form: "pd.DataFrame | None") -> float:
    """
    Goals per game from qualifying form, normalised so 1.8 gpg → factor 1.0.
    Applies confederation strength so that, e.g., Egypt's AFCON gpg is weighted
    at 0.58× before comparison with UEFA teams (UEFA = 1.0 baseline).
    Returns factor in [0.5, 1.8]; falls back to 1.0 (neutral) when no data.
    """
    if form is None or (hasattr(form, "empty") and form.empty) or not team:
        return 1.0
    norm_team = team.strip().lower()
    row = form[form["team"].astype(str).str.strip().str.lower() == norm_team]
    if row.empty:
        return 1.0
    try:
        p = float(row.iloc[0]["p"]) if row.iloc[0]["p"] else 0
        f = float(row.iloc[0]["f"]) if row.iloc[0]["f"] else 0
        if p > 0:
            gpg = f / p
            conf = TEAM_CONFEDERATION.get(team.strip(), "UEFA")
            conf_weight = CONFEDERATION_STRENGTH.get(conf, 1.0)
            effective_gpg = gpg * conf_weight
            return min(1.8, max(0.5, effective_gpg / 1.8))
    except (ValueError, TypeError):
        pass
    return 1.0


# ── Name parsing helper ───────────────────────────────────────────────────────

def _parse_player_names(raw: str) -> list[str]:
    """Extract normalised player names from a goalscorers/assists string."""
    names = []
    for entry in raw.split(","):
        name = re.sub(r"\(.*?\)", "", entry)
        name = re.sub(r"\d+[''`′]?", "", name)
        name = name.strip()
        if name:
            names.append(_norm(name))
    return names


# ── Actual tournament results ─────────────────────────────────────────────────

def compute_actual_stats(results: "pd.DataFrame | None") -> dict:
    """
    Derive team and player performance stats from actual tournament results.

    Returns a dict with:
      team_games    – {team → games played}
      team_attack   – {team → actual goals scored per game}
      team_defense  – {team → actual goals conceded per game}
      player_goals  – {normalised_player_name → goals scored in tournament}

    All values default to empty dicts when no results exist yet.
    The calling code blends these against pre-tournament priors with weight
    proportional to games played (prior_weight = 5 equivalent games).
    """
    empty: dict = {"team_games": {}, "team_attack": {}, "team_defense": {}, "player_goals": {}}
    if results is None or (hasattr(results, "empty") and results.empty):
        return empty

    team_gf: dict[str, int] = {}
    team_ga: dict[str, int] = {}
    team_games: dict[str, int] = {}
    player_goals: dict[str, int] = {}
    player_assists: dict[str, int] = {}

    for _, row in results.iterrows():
        home = str(row.get("home_team", "")).strip()
        away = str(row.get("away_team", "")).strip()
        hs_raw = str(row.get("home_score", "")).strip()
        as_raw = str(row.get("away_score", "")).strip()
        if not hs_raw or not as_raw or hs_raw in ("", "nan") or as_raw in ("", "nan"):
            continue
        try:
            hs = int(float(hs_raw))
            as_ = int(float(as_raw))
        except (ValueError, TypeError):
            continue
        if not home or not away or home.startswith("Winner") or home.startswith("1st") or home.startswith("2nd"):
            continue

        for t, gf, ga in [(home, hs, as_), (away, as_, hs)]:
            team_gf[t]    = team_gf.get(t, 0) + gf
            team_ga[t]    = team_ga.get(t, 0) + ga
            team_games[t] = team_games.get(t, 0) + 1

        gs_raw = str(row.get("goalscorers", "")).strip()
        if gs_raw and gs_raw.lower() not in ("", "nan"):
            for n in _parse_player_names(gs_raw):
                player_goals[n] = player_goals.get(n, 0) + 1

        as_raw_str = str(row.get("assists", "")).strip()
        if as_raw_str and as_raw_str.lower() not in ("", "nan"):
            for n in _parse_player_names(as_raw_str):
                player_assists[n] = player_assists.get(n, 0) + 1

    team_attack  = {t: team_gf.get(t, 0) / g for t, g in team_games.items()}
    team_defense = {t: team_ga.get(t, 0) / g for t, g in team_games.items()}
    return {
        "team_games":     team_games,
        "team_attack":    team_attack,
        "team_defense":   team_defense,
        "player_goals":   player_goals,
        "player_assists": player_assists,
    }


def compute_recent_form(results: "pd.DataFrame | None", n_recent_days: int = 2) -> set:
    """
    Returns a set of normalised player names who scored or assisted in the
    most recent n_recent_days of completed match dates.

    Used to apply a short-term form boost (×1.15) in expected_matchday_points.
    The boost is intentionally mild and self-correcting — as more games are
    played, the actual_stats blending in expected_matchday_points dominates.
    """
    if results is None or (hasattr(results, "empty") and results.empty):
        return set()

    played_rows = []
    for _, row in results.iterrows():
        hs = str(row.get("home_score", "")).strip()
        as_ = str(row.get("away_score", "")).strip()
        if hs and as_ and hs not in ("", "nan") and as_ not in ("", "nan"):
            try:
                int(float(hs))
                played_rows.append(row)
            except (ValueError, TypeError):
                pass

    if not played_rows:
        return set()

    played_df = pd.DataFrame(played_rows)
    dates = sorted(played_df["date"].astype(str).str.strip().unique(), reverse=True)
    recent_dates = set(dates[:n_recent_days])
    recent = played_df[played_df["date"].astype(str).str.strip().isin(recent_dates)]

    hot: set = set()
    for _, row in recent.iterrows():
        for col in ("goalscorers", "assists"):
            raw = str(row.get(col, "")).strip()
            if raw and raw.lower() != "nan":
                for name in _parse_player_names(raw):
                    hot.add(name)
    return hot


# ── Group standings and knockout advancement ──────────────────────────────────

def compute_group_standings(
    results: "pd.DataFrame | None", groups: pd.DataFrame
) -> dict:
    """
    Parse actual match results into a per-team group standings dict.

    Returns {team → {pts, gd, gf, ga, played}}.
    Only counts matches where both scores are known (played matches).
    """
    all_teams = groups["team"].astype(str).str.strip().tolist()
    standings: dict = {
        t: {"pts": 0, "gd": 0, "gf": 0, "ga": 0, "played": 0} for t in all_teams
    }

    if results is None or (hasattr(results, "empty") and results.empty):
        return standings

    for _, row in results.iterrows():
        home  = str(row.get("home_team", "")).strip()
        away  = str(row.get("away_team", "")).strip()
        hs_r  = str(row.get("home_score", "")).strip()
        as_r  = str(row.get("away_score", "")).strip()
        if not hs_r or hs_r in ("", "nan") or not as_r or as_r in ("", "nan"):
            continue
        try:
            hs, as_ = int(float(hs_r)), int(float(as_r))
        except (ValueError, TypeError):
            continue

        for team, gf, ga in [(home, hs, as_), (away, as_, hs)]:
            if team not in standings:
                continue
            standings[team]["gf"]     += gf
            standings[team]["ga"]     += ga
            standings[team]["gd"]     += gf - ga
            standings[team]["played"] += 1
            if gf > ga:
                standings[team]["pts"] += 3
            elif gf == ga:
                standings[team]["pts"] += 1

    return standings


def compute_advance_probability(
    team: str,
    standings: "dict | None",
    groups: pd.DataFrame,
) -> float:
    """
    Estimate the probability that a team advances from the group stage.

    WC 2026 format: top 2 from each of 12 groups advance automatically (24 teams);
    the best 8 third-place teams also advance (total 32 in R32).

    Returns a float in [0.0, 1.0]:
      1.0 = already qualified (top-2 after matchday 3)
      0.0 = already eliminated (4th place after matchday 3)
      in-between = estimated probability during group stage

    Used to prioritise long-lived players in transfer decisions and to flag
    high-risk squad players before the group stage ends.
    """
    if standings is None:
        return 0.5

    grp_row = groups[groups["team"].astype(str).str.strip() == team.strip()]
    if grp_row.empty:
        return 0.5
    team_group = str(grp_row.iloc[0]["group"]).strip()
    group_teams = (
        groups[groups["group"].astype(str).str.strip() == team_group]["team"]
        .astype(str).str.strip().tolist()
    )

    team_data = standings.get(team, {"pts": 0, "gd": 0, "gf": 0, "played": 0})
    pts    = int(team_data.get("pts", 0))
    played = int(team_data.get("played", 0))

    if played == 0:
        # No results yet — FIFA ranking is the prior estimate.
        # Linear calibration: rank 1→94%, rank 10→87%, rank 25→72%, rank 50→47%, rank 80→17%.
        # WC draw is seeded so top teams rarely face each other in groups, which is why
        # even rank 10 teams have a high probability of finishing top 2 in their group.
        try:
            rank = float(grp_row.iloc[0]["fifa_ranking"])
        except (ValueError, TypeError, KeyError):
            rank = 50.0
        return round(min(0.94, max(0.15, 0.97 - rank * 0.010)), 3)

    # For 1-2 games played, blend the rank-based prior with the result signal.
    # A win by Spain means more than a win by Haiti; rank stays relevant.
    try:
        rank = float(grp_row.iloc[0]["fifa_ranking"])
    except (ValueError, TypeError, KeyError):
        rank = 50.0
    rank_prior = min(0.94, max(0.15, 0.97 - rank * 0.010))

    if played == 1:
        result_signal = {3: 0.76, 1: 0.56, 0: 0.34}.get(pts, 0.50)
        return round(0.40 * rank_prior + 0.60 * result_signal, 3)

    if played == 2:
        result_signal = {6: 0.97, 4: 0.85, 3: 0.70, 2: 0.48, 1: 0.28, 0: 0.05}.get(pts, 0.50)
        return round(0.25 * rank_prior + 0.75 * result_signal, 3)

    # Matchday 3 completed — group standing is final
    group_data = {t: standings.get(t, {"pts": 0, "gd": 0, "gf": 0}) for t in group_teams}
    sorted_teams = sorted(
        group_data.keys(),
        key=lambda t: (
            group_data[t].get("pts", 0),
            group_data[t].get("gd", 0),
            group_data[t].get("gf", 0),
        ),
        reverse=True,
    )
    try:
        position = sorted_teams.index(team) + 1
    except ValueError:
        return 0.5

    if position <= 2:
        return 1.0  # automatically qualified

    if position == 3:
        # 8 of 12 third-place teams advance — likelihood depends on total points
        if pts >= 6: return 0.90
        if pts >= 5: return 0.72
        if pts >= 4: return 0.55
        if pts >= 3: return 0.38
        if pts >= 2: return 0.20
        return 0.05

    return 0.0  # 4th place — eliminated


def _group_stage_date_range(
    fixtures: pd.DataFrame,
) -> "tuple":
    """Return (first_date, last_date) of group stage fixtures as datetime.date objects."""
    gs_mask = (
        fixtures["matchday"].astype(str).str.strip().str.match(r"^[123]\.?0?$") |
        fixtures["stage"].astype(str).str.strip().str.lower().isin(["group stage", "group"])
    )
    gs_fx = fixtures[gs_mask]
    dates = []
    for d in gs_fx["date"].astype(str).str.strip():
        try:
            dates.append(datetime.date.fromisoformat(d))
        except ValueError:
            pass
    if not dates:
        return None, None
    return min(dates), max(dates)


def compute_ko_win_prob(
    team: str,
    fixtures: pd.DataFrame,
    groups: pd.DataFrame,
    advance_probs: "dict | None" = None,
) -> float:
    """
    Probability that a team survives their next knockout match.

    Draws lead to extra time / penalties — modelled as 50% each team advances.
    Falls back to advance_probs (group-stage estimate) if no knockout fixture found.
    """
    team = team.strip()
    unplayed = fixtures
    if "home_score" in fixtures.columns:
        unplayed = fixtures[
            fixtures["home_score"].isna() | (fixtures["home_score"].astype(str).str.strip() == "")
        ]

    team_fx = unplayed[
        (unplayed["home_team"].astype(str).str.strip() == team) |
        (unplayed["away_team"].astype(str).str.strip() == team)
    ].copy()

    if team_fx.empty:
        return float((advance_probs or {}).get(team, 0.5))

    try:
        team_fx["_d"] = pd.to_datetime(team_fx["date"], errors="coerce")
        team_fx = team_fx.dropna(subset=["_d"]).sort_values("_d")
    except Exception:
        return float((advance_probs or {}).get(team, 0.5))

    if team_fx.empty:
        return float((advance_probs or {}).get(team, 0.5))

    next_fx = team_fx.iloc[0]
    stage = str(next_fx.get("stage", "")).strip().lower()
    is_ko_fixture = bool(stage and "group" not in stage and "matchday" not in stage)

    if not is_ko_fixture:
        return float((advance_probs or {}).get(team, 0.5))

    opp_col = "away_team" if str(next_fx.get("home_team", "")).strip() == team else "home_team"
    opp = str(next_fx.get(opp_col, "")).strip()
    if not opp:
        return float((advance_probs or {}).get(team, 0.5))

    team_rank = get_team_ranking(team, groups)
    opp_rank  = get_team_ranking(opp, groups)
    team_rank_eff = max(1.0, team_rank - HOME_RANK_BOOST) if team in HOST_NATIONS else team_rank
    win_p, draw_p, _ = _result_probs(team_rank_eff, opp_rank)
    return round(win_p + draw_p * 0.5, 3)


# ── Single-day scoring helper ─────────────────────────────────────────────────

def _score_for_date(
    player: pd.Series,
    day_fixtures: pd.DataFrame,
    groups: pd.DataFrame,
    form: "pd.DataFrame | None" = None,
    actual_stats: "dict | None" = None,
    form_stats: "dict | None" = None,
    recent_form: "set | None" = None,
) -> float:
    """
    Expected points for a player on one specific calendar day.
    Returns 0.0 if the player's team has no game that day.
    """
    team = str(player.get("team", "")).strip()
    if not team or day_fixtures.empty:
        return 0.0
    team_fx = day_fixtures[
        (day_fixtures["home_team"].astype(str).str.strip() == team) |
        (day_fixtures["away_team"].astype(str).str.strip() == team)
    ]
    if team_fx.empty:
        return 0.0
    return expected_matchday_points(
        player, team_fx, groups, next_n=1,
        form=form, actual_stats=actual_stats, form_stats=form_stats, recent_form=recent_form,
    )


# ── Expected points estimation ─────────────────────────────────────────────────

def expected_matchday_points(
    player: pd.Series,
    fixtures: pd.DataFrame,
    groups: pd.DataFrame,
    next_n: int = 3,
    form: "pd.DataFrame | None" = None,
    actual_stats: "dict | None" = None,
    form_stats: "dict | None" = None,
    recent_form: "set | None" = None,
) -> float:
    """
    Estimate expected Futispörssi points per game over the next N fixtures.

    Components:
      - Appearance (2 pts)
      - Result bonus (win/draw/loss by position)
      - Clean sheet probability × CS points  ← blended with actual concede rate
      - GK saves flat estimate
      - DEF goals-against penalty
      - Penalty taker bonus
      - Set-piece role bonus
      - Goal/assist contribution: BASE_GOAL_RATE × opponent_factor × team_attack_factor
        ← attack_factor and base_goal_rate blended with actual tournament data when available
        (DEF defensive MID 30% penalty applied to goal rate)

    actual_stats (from compute_actual_stats):
      When actual results exist, the model blends pre-tournament priors with tournament
      data.  Prior weight = 5 equivalent games.  After 5 real games the split is 50/50;
      after 10 games actual data dominates.  This makes the model self-correcting as the
      tournament progresses.
    """
    pos = str(player.get("position", "")).strip().upper()
    if pos not in POSITION_POINTS:
        pos = "MID"
    pp = POSITION_POINTS[pos]
    team = str(player.get("team", "")).strip() if pd.notna(player.get("team", "")) else ""

    unplayed = fixtures
    if "home_score" in fixtures.columns:
        unplayed = fixtures[
            fixtures["home_score"].isna() | (fixtures["home_score"].astype(str).str.strip() == "")
        ]
    team_fx = unplayed[
        (unplayed["home_team"].astype(str).str.strip() == team) |
        (unplayed["away_team"].astype(str).str.strip() == team)
    ].head(next_n)

    if team_fx.empty or not team:
        # No team info — neutral fallback
        return round(2.5 + (1.0 if pos in ("GK", "DEF") else 0.5), 2)

    # ── Player attributes ─────────────────────────────────────────────────────
    spr = str(player.get("set_piece_role", "")).strip().lower()
    pt  = str(player.get("penalty_taker", "")).strip().lower()
    is_pen_taker = pt in ("primary", "secondary")
    pen_primary  = pt == "primary"
    has_sp_role  = spr not in ("no", "none", "")

    # Base goal rate; apply 30% penalty for likely defensive midfielders
    base_goal_rate = BASE_GOAL_RATE.get(pos, 0.08)
    if pos == "MID" and not has_sp_role and not is_pen_taker:
        base_goal_rate *= 0.70

    # Base assist rate — independent of goal rate; MIDs are playmakers, FWDs are finishers
    base_assist_rate = BASE_ASSIST_RATE.get(pos, 0.06)
    if pos == "MID" and not has_sp_role and not is_pen_taker:
        base_assist_rate *= 0.60  # defensive MIDs set up fewer chances

    # Team attack strength from qualifying form (neutral = 1.0)
    attack_factor = get_team_attack_rate(team, form)

    # Blend with pre-tournament recent form (last 5 matches) — gives better
    # initial priors before tournament results exist.  Recent 5-match GPG is
    # weighted 2× vs the qualification-history prior.
    if form_stats:
        recent_gpg = form_stats.get("team_gpg", {}).get(team)
        if recent_gpg is not None:
            prior_gpg = attack_factor * 1.8
            blended_gpg = (prior_gpg + recent_gpg * 2) / 3
            attack_factor = min(1.8, max(0.5, blended_gpg / 1.8))

    # ── Blend priors with actual tournament data ──────────────────────────────
    # Prior weight = 5 equivalent games.  Blending shifts toward actual data
    # as the tournament progresses; with 0 games played nothing changes.
    _PRIOR_GAMES = 5
    _actual_games = 0
    if actual_stats:
        _actual_games = actual_stats.get("team_games", {}).get(team, 0)

    if _actual_games > 0:
        _blend = _actual_games / (_PRIOR_GAMES + _actual_games)

        # Attack factor: blend prior GPG with actual GPG
        actual_gpg_team = actual_stats.get("team_attack", {}).get(team, 0.0)  # type: ignore[union-attr]
        prior_gpg = attack_factor * 1.8
        blended_gpg = prior_gpg * (1 - _blend) + actual_gpg_team * _blend
        attack_factor = min(1.8, max(0.5, blended_gpg / 1.8))

        # Player goal rate: blend base rate with actual goals/game for this player
        pnorm = _norm(str(player.get("name", "")))
        p_goals = actual_stats.get("player_goals", {}).get(pnorm, 0)  # type: ignore[union-attr]
        if p_goals == 0:
            p_parts = pnorm.split()
            if p_parts:
                p_goals = actual_stats.get("player_goals", {}).get(p_parts[0], 0)  # type: ignore[union-attr]
        if p_goals > 0:
            actual_player_gpg = p_goals / _actual_games
            base_goal_rate = base_goal_rate * (1 - _blend) + actual_player_gpg * _blend

        # Player assist rate: blend base rate with actual assists/game
        p_assists = actual_stats.get("player_assists", {}).get(pnorm, 0)  # type: ignore[union-attr]
        if p_assists == 0:
            p_parts = pnorm.split()
            if p_parts:
                p_assists = actual_stats.get("player_assists", {}).get(p_parts[0], 0)  # type: ignore[union-attr]
        if p_assists > 0:
            actual_player_apg = p_assists / _actual_games
            base_assist_rate = base_assist_rate * (1 - _blend) + actual_player_apg * _blend

    # Hot-streak bonus: ×1.15 on attack rates for players who scored/assisted in
    # the most recent completed match days.  Mild boost — actual_stats blending
    # takes over as the tournament progresses.
    if recent_form:
        pnorm = _norm(str(player.get("name", "")))
        p_parts = pnorm.split()
        last_name = p_parts[0] if p_parts else ""
        # Goalscorer strings use "LastName Initial." (e.g. "quinones j."), so we
        # match by checking if any hot-streak entry starts with the player's last name.
        in_hot_streak = (
            pnorm in recent_form or
            (last_name and any(rf.startswith(last_name) for rf in recent_form))
        )
        if in_hot_streak:
            base_goal_rate   *= 1.15
            base_assist_rate *= 1.15

    # ── Per-fixture loop ──────────────────────────────────────────────────────
    total_pts = 0.0
    for _, row in team_fx.iterrows():
        opp = row["away_team"] if str(row["home_team"]).strip() == team else row["home_team"]
        team_rank = get_team_ranking(team, groups)
        opp_rank  = get_team_ranking(str(opp).strip(), groups)

        # Host nation home advantage: USA, Canada, and Mexico play in front of
        # home crowds throughout WC 2026.  Treat them as HOME_RANK_BOOST FIFA
        # spots stronger when computing result probabilities.
        team_rank_eff = max(1.0, team_rank - HOME_RANK_BOOST) if team in HOST_NATIONS else team_rank
        win_p, draw_p, loss_p = _result_probs(team_rank_eff, opp_rank)

        # Knockout fixtures: tighter games → higher CS, fewer goals scored
        stage = str(row.get("stage", "")).strip().lower()
        is_knockout = bool(stage and "group" not in stage and "matchday" not in stage)

        pts = 2.0  # appearance + 60 min

        # Result bonus
        pts += win_p * pp["win"] + draw_p * pp["draw"] + loss_p * pp["loss"]

        # Clean sheet probability (prior: from win/draw model)
        cs_prob = win_p * 0.60 + draw_p * 0.20
        if is_knockout:
            cs_prob = min(0.75, cs_prob * 1.25)  # knockout games are tighter

        # Blend CS with pre-tournament recent form (weighted 2× vs rank-based prior)
        if form_stats:
            recent_concede_gpg = form_stats.get("team_concede_gpg", {}).get(team)
            if recent_concede_gpg is not None:
                recent_cs = math.exp(-max(0.1, float(recent_concede_gpg)))
                cs_prob = (cs_prob + recent_cs * 2) / 3

        # Blend CS probability with actual concede rate (Poisson P(0 conceded))
        if _actual_games > 0:
            _blend = _actual_games / (_PRIOR_GAMES + _actual_games)
            actual_concede_gpg = actual_stats.get("team_defense", {}).get(team, 0.0)  # type: ignore[union-attr]
            actual_cs_prob = math.exp(-max(0.1, actual_concede_gpg))
            cs_prob = cs_prob * (1 - _blend) + actual_cs_prob * _blend

        pts += cs_prob * pp["clean_sheet"]

        # GK saves — scale with how dangerous the opponent is.
        # A GK facing Brazil faces ~9 SoT; one facing Panama faces ~2.
        # We subtract expected goals conceded to get expected saves, then
        # map to the save-tier point bands using Poisson expectation.
        if pos == "GK":
            # Square-root scaling: rank 5 (Brazil) is dangerous but not 10× rank 50.
            # sqrt(50/rank) gives: rank5→3.16, rank20→1.58, rank50→1.0, rank100→0.71
            opp_attack_threat = min(1.8, max(0.4, (50.0 / max(opp_rank, 1)) ** 0.5))
            exp_sot_against = AVG_SOT_AGAINST * opp_attack_threat
            exp_ga_gk = loss_p * 2.0 + draw_p * 0.5
            exp_saves = max(0.3, exp_sot_against - exp_ga_gk)
            pts += _expected_tier_pts(exp_saves)

        # DEF goals-against penalty
        if pos == "DEF":
            expected_ga = loss_p * 2.5 + draw_p * 0.5
            if expected_ga < 2:
                pts -= 0.5
            elif expected_ga < 4:
                pts -= 1.0
            else:
                pts -= 1.5

        # Penalty taker bonus
        if is_pen_taker:
            pen_rate = 0.4 if pen_primary else 0.2
            pts += pen_rate * (0.80 * (pp["goal"] - 2) + 0.20 * -2)

        # Set-piece role bonus
        if spr in ("both", "free kicks"):
            pts += 0.3 * pp["assist"]
        if spr in ("both", "corners"):
            pts += 0.2 * pp["assist"]

        # Goal/assist contribution — separate rates, both scaled by fixture context
        opp_factor = _opp_goal_factor(opp_rank)
        if is_knockout:
            opp_factor *= 0.85
        exp_goals   = base_goal_rate   * opp_factor * attack_factor
        exp_assists = base_assist_rate * opp_factor * attack_factor
        pts += exp_goals   * pp["goal"]
        pts += exp_assists * pp["assist"]

        # Shots-on-target bonus for outfield players.
        # Every shot a player takes that's on target scores points even if saved.
        # Expected SoT = exp_goals / conversion_rate (goals are a subset of SoT).
        if pos in ("FWD", "MID", "DEF"):
            conversion = SHOT_CONVERSION.get(pos, 0.12)
            if conversion > 0:
                exp_sot = exp_goals / conversion
                pts += _expected_tier_pts(exp_sot)

        total_pts += pts

    return round(total_pts / len(team_fx), 2)


# ── Squad optimizer ────────────────────────────────────────────────────────────

MIN_PLAYER_VALUE = 275_000  # reserved per remaining slot when picking


def _pick_for_formation(
    players_df: pd.DataFrame, n_def: int, n_mid: int, n_fwd: int
) -> pd.DataFrame | None:
    """Greedy budget-aware squad selection. Returns 11-player DataFrame or None."""
    budget = BUDGET
    chosen: list[pd.Series] = []

    slots = [("GK", 1), ("DEF", n_def), ("MID", n_mid), ("FWD", n_fwd)]
    slots_remaining = sum(s for _, s in slots)

    for pos, count in slots:
        slots_remaining -= count
        reserve = slots_remaining * MIN_PLAYER_VALUE
        pos_budget = budget - reserve

        candidates = (
            players_df[players_df["position"].str.upper() == pos]
            .sort_values("exp_pts", ascending=False)
        )

        selected: list[pd.Series] = []
        for _, row in candidates.iterrows():
            if len(selected) >= count:
                break
            val = parse_value(row["value"])
            if val > 0 and val <= pos_budget:
                selected.append(row)
                pos_budget -= val
                budget -= val

        if len(selected) < count:
            return None
        chosen.extend(selected)

    return pd.DataFrame(chosen).reset_index(drop=True)


def recommend_best_squad(
    players: pd.DataFrame,
    fixtures: pd.DataFrame,
    groups: pd.DataFrame,
    lineups: pd.DataFrame,
    form: "pd.DataFrame | None" = None,
    actual_stats: "dict | None" = None,
    form_stats: "dict | None" = None,
    recent_form: "set | None" = None,
) -> dict | None:
    """
    Returns the optimal 11-player squad within budget.

    Result keys:
      squad            – DataFrame with exp_pts column
      formation        – e.g. "4-3-3"
      captain          – player name string
      captain_pts      – expected pts as captain
      total_pts        – squad total expected pts/game
      budget_used      – int euros
      enriched_players – all players with team + exp_pts (for transfer page)
    """
    if players.empty:
        return None

    enriched = _enrich_with_team(players, lineups)
    # Keep only players matched to a lineup entry — non-starters are irrelevant
    enriched = enriched[enriched["team"].astype(str).str.strip() != ""].copy()
    enriched["exp_pts"] = enriched.apply(
        lambda r: expected_matchday_points(r, fixtures, groups, form=form, actual_stats=actual_stats, form_stats=form_stats, recent_form=recent_form), axis=1
    )

    best: dict | None = None
    for formation in VALID_FORMATIONS:
        d, m, f = [int(x) for x in formation.split("-")]
        squad = _pick_for_formation(enriched, d, m, f)
        if squad is None:
            continue
        total_pts = squad["exp_pts"].sum()
        if best is None or total_pts > best["total_pts"]:
            best = {
                "squad": squad,
                "formation": formation,
                "total_pts": round(total_pts, 1),
                "budget_used": int(squad["value"].apply(parse_value).sum()),
            }

    if best is None:
        return None

    cap_idx = best["squad"]["exp_pts"].idxmax()
    cap_row = best["squad"].loc[cap_idx]
    best["captain"] = cap_row["name"]
    best["captain_pts"] = round(float(cap_row["exp_pts"]) * CAPTAIN_MULTIPLIER, 1)
    best["enriched_players"] = enriched
    return best


# ── User squad loader ──────────────────────────────────────────────────────────

def load_user_squad(
    players: pd.DataFrame,
    lineups: pd.DataFrame,
    fixtures: pd.DataFrame,
    groups: pd.DataFrame,
    form: "pd.DataFrame | None" = None,
    actual_stats: "dict | None" = None,
    form_stats: "dict | None" = None,
    today_str: str = "",
    recent_form: "set | None" = None,
) -> dict | None:
    """
    Build a squad dict (same shape as recommend_best_squad result) from players
    where in_squad == 'True'.  Returns None if fewer than 11 players are flagged.

    Captain is picked for TODAY: the squad player playing today with the highest
    expected points for that specific game.  Falls back to next game day if no
    squad player has a fixture today.
    """
    import datetime as _dt

    mask = players["in_squad"].astype(str).str.strip().str.lower() == "true"
    user_players = players[mask].copy()
    if len(user_players) < 11:
        return None

    try:
        today = _dt.date.fromisoformat(today_str) if today_str else _dt.date.today()
    except ValueError:
        today = _dt.date.today()

    enriched_all = _enrich_with_team(players, lineups)
    user_enriched = enriched_all[mask].copy()

    # Overall expected pts (avg over next few games) — for display on cards
    user_enriched["exp_pts"] = user_enriched.apply(
        lambda r: expected_matchday_points(
            r, fixtures, groups, form=form, actual_stats=actual_stats, form_stats=form_stats, recent_form=recent_form,
        ),
        axis=1,
    )

    # Today's expected pts — for captain selection
    today_iso = today.isoformat()
    today_fx = (
        fixtures[fixtures["date"].astype(str).str.strip() == today_iso]
        if not fixtures.empty else pd.DataFrame()
    )
    user_enriched["today_pts"] = user_enriched.apply(
        lambda r: _score_for_date(
            r, today_fx, groups, form=form, actual_stats=actual_stats, form_stats=form_stats, recent_form=recent_form,
        ),
        axis=1,
    )

    squad = user_enriched.reset_index(drop=True)

    # Captain: pick the player playing TODAY with the highest today_pts.
    # If nobody plays today, look ahead day-by-day until we find a game day.
    cap_idx = None
    playing_today = squad[squad["today_pts"] > 0]
    if not playing_today.empty:
        cap_idx = playing_today["today_pts"].idxmax()
        cap_pts_val = float(squad.loc[cap_idx, "today_pts"]) * CAPTAIN_MULTIPLIER
    else:
        # Look ahead up to 7 days for the next game day any squad player plays
        for ahead in range(1, 8):
            next_date = (today + _dt.timedelta(days=ahead)).isoformat()
            nxt_fx = fixtures[fixtures["date"].astype(str).str.strip() == next_date] if not fixtures.empty else pd.DataFrame()
            next_pts = squad.apply(
                lambda r: _score_for_date(r, nxt_fx, groups, form=form, actual_stats=actual_stats, form_stats=form_stats, recent_form=recent_form),
                axis=1,
            )
            if next_pts.max() > 0:
                cap_idx = next_pts.idxmax()
                cap_pts_val = float(next_pts[cap_idx]) * CAPTAIN_MULTIPLIER
                break

    if cap_idx is None:
        # Ultimate fallback: highest overall exp_pts
        cap_idx = squad["exp_pts"].idxmax()
        cap_pts_val = float(squad.loc[cap_idx, "exp_pts"]) * CAPTAIN_MULTIPLIER

    cap_row = squad.loc[cap_idx]
    n_def = int((squad["position"].str.upper() == "DEF").sum())
    n_mid = int((squad["position"].str.upper() == "MID").sum())
    n_fwd = int((squad["position"].str.upper() == "FWD").sum())
    formation = f"{n_def}-{n_mid}-{n_fwd}"

    return {
        "squad": squad,
        "formation": formation,
        "total_pts": round(float(squad["exp_pts"].sum()), 1),
        "budget_used": int(squad["value"].apply(parse_value).sum()),
        "captain": cap_row["name"],
        "captain_pts": round(cap_pts_val, 1),
        "enriched_players": enriched_all,
        "is_user_squad": True,
    }


def squad_coverage_gaps(
    squad: pd.DataFrame,
    fixtures: pd.DataFrame,
) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """
    Return (gap_dates, covered_list) for the group stage (June dates).

    gap_dates    – dates where no squad player's team plays
    covered_list – [(date, [teams_playing]), ...]  for days with coverage
    """
    squad_teams = set(
        squad["team"].astype(str).str.strip().tolist()
    ) - {"", "nan"}

    june_dates = sorted(
        d for d in fixtures["date"].astype(str).str.strip().unique()
        if d.startswith("2026-06")
    )

    gaps: list[str] = []
    covered: list[tuple[str, list[str]]] = []

    for date in june_dates:
        day_fx = fixtures[fixtures["date"].astype(str).str.strip() == date]
        home = set(day_fx["home_team"].astype(str).str.strip())
        away = set(day_fx["away_team"].astype(str).str.strip())
        playing = (home | away) & squad_teams
        if playing:
            covered.append((date, sorted(playing)))
        else:
            gaps.append(date)

    return gaps, covered


# ── Transfer suggestions ───────────────────────────────────────────────────────

def recommend_transfers(
    squad_df: pd.DataFrame,
    all_players: pd.DataFrame,
    fixtures: pd.DataFrame,
    groups: pd.DataFrame,
    n_suggestions: int = 5,
    position_filter: str | None = None,
    form: "pd.DataFrame | None" = None,
    actual_stats: "dict | None" = None,
    form_stats: "dict | None" = None,
    today_str: str = "",
    recent_form: "set | None" = None,
) -> dict:
    """
    Given current squad, suggest transfers in and out.
    Returns {"out": [...], "in": [...]}
    """
    import datetime as _dt

    if squad_df.empty or all_players.empty:
        return {"out": [], "in": []}

    squad_names = set(squad_df["name"].astype(str).str.strip().tolist())
    available = all_players[~all_players["name"].astype(str).str.strip().isin(squad_names)].copy()

    if position_filter:
        available = available[available["position"].str.upper() == position_filter.upper()]
        squad_pos = squad_df[squad_df["position"].str.upper() == position_filter.upper()].copy()
    else:
        squad_pos = squad_df.copy()

    # Available players: rank by NEXT SINGLE FIXTURE only.
    # The Explorer answers "who is best to buy RIGHT NOW?" — future games should not
    # inflate players whose immediate fixture is tough (e.g., Egypt vs Belgium).
    if "exp_pts" not in available.columns:
        available["exp_pts"] = available.apply(
            lambda r: expected_matchday_points(r, fixtures, groups, next_n=1, form=form, actual_stats=actual_stats, form_stats=form_stats, recent_form=recent_form), axis=1
        )
    # Squad: keep next_n=3 for sell-ranking — you care about a player's full upcoming
    # schedule when deciding whether to move them on.
    if "exp_pts" not in squad_pos.columns:
        squad_pos["exp_pts"] = squad_pos.apply(
            lambda r: expected_matchday_points(r, fixtures, groups, next_n=3, form=form, actual_stats=actual_stats, form_stats=form_stats, recent_form=recent_form), axis=1
        )

    # Soft protection: players whose team plays within the next 2 days get a bonus
    # equal to their expected near-game pts added to their effective score. This makes
    # them appear less attractive as sell targets — without hard-blocking them in case
    # the gain on the other side is very large.
    squad_pos = squad_pos.copy()
    near_bonus: dict[str, float] = {}
    try:
        today_d = _dt.date.fromisoformat(today_str) if today_str else _dt.date.today()
        for offset in range(3):  # today + 0, 1, 2 days ahead
            check_str = (today_d + _dt.timedelta(days=offset)).isoformat()
            day_fx_near = fixtures[fixtures["date"].astype(str).str.strip() == check_str]
            if day_fx_near.empty:
                continue
            for _, row in squad_pos.iterrows():
                name = str(row.get("name", ""))
                if name in near_bonus:
                    continue
                near_pts = _score_for_date(row, day_fx_near, groups, form, actual_stats=actual_stats, form_stats=form_stats, recent_form=recent_form)
                if near_pts > 0:
                    near_bonus[name] = near_pts
    except (ValueError, TypeError):
        pass

    squad_pos["_protected_pts"] = squad_pos.apply(
        lambda r: float(r.get("exp_pts", 0)) + near_bonus.get(str(r.get("name", "")), 0.0),
        axis=1,
    )
    worst_out = squad_pos.nsmallest(n_suggestions, "_protected_pts")

    # Budget constraint: in-player must fit within cap after selling the weakest out-player.
    budget_used = squad_budget_used(squad_df)
    min_out_val = worst_out["value"].apply(parse_value).min() if not worst_out.empty else 0
    budget_slack = BUDGET - budget_used + min_out_val
    affordable = available[available["value"].apply(parse_value) <= budget_slack]

    top_in = affordable.nlargest(n_suggestions, "exp_pts")

    _today_iso = today_str or import_dt.date.today().isoformat() if (import_dt := __import__("datetime")) else ""

    def _next_game(team: str) -> tuple[str, str]:
        """(date, opponent) for the team's next upcoming fixture."""
        if not team:
            return "", ""
        _t = today_str or __import__("datetime").date.today().isoformat()
        up = fixtures[
            ((fixtures["home_team"].astype(str).str.strip() == team) |
             (fixtures["away_team"].astype(str).str.strip() == team)) &
            (fixtures["date"].astype(str).str.strip() >= _t)
        ].sort_values("date")
        if up.empty:
            return "", ""
        fx = up.iloc[0]
        opp = (str(fx["away_team"]).strip()
               if str(fx["home_team"]).strip() == team
               else str(fx["home_team"]).strip())
        return str(fx["date"]).strip(), opp

    def row_to_dict(row):
        pt  = str(row.get("penalty_taker", "")).lower()
        spr = str(row.get("set_piece_role", "")).lower()
        reasons = []
        if pt in ("primary", "secondary"):
            reasons.append(f"Penalty taker ({pt})")
        if spr not in ("no", "none", ""):
            reasons.append(f"Set pieces: {spr}")
        team = str(row.get("team", ""))
        next_date, next_opp = _next_game(team)
        if team:
            diff = fixture_difficulty(team, fixtures, groups)
            label, _ = difficulty_label(diff)
            reasons.append(f"{label} upcoming fixtures (avg rank {diff:.0f})")
        return {
            "name":      row.get("name", ""),
            "position":  str(row.get("position", "")).upper(),
            "value":     row.get("value", "?"),
            "exp_pts":   round(float(row.get("exp_pts", 0)), 1),
            "team":      team,
            "next_date": next_date,
            "next_opp":  next_opp,
            "reason":    "  ·  ".join(reasons) if reasons else "—",
        }

    return {
        "out": [row_to_dict(r) for _, r in worst_out.iterrows()],
        "in":  [row_to_dict(r) for _, r in top_in.iterrows()],
    }


# ── Fixture difficulty table for squad ────────────────────────────────────────

def squad_fixture_table(
    squad_df: pd.DataFrame,
    fixtures: pd.DataFrame,
    groups: pd.DataFrame,
    next_n: int = 4,
) -> pd.DataFrame:
    """Per-player fixture breakdown for the squad."""
    if squad_df.empty or fixtures.empty:
        return pd.DataFrame()

    unplayed = fixtures
    if "home_score" in fixtures.columns:
        unplayed = fixtures[
            fixtures["home_score"].isna() | (fixtures["home_score"].astype(str).str.strip() == "")
        ]

    rows = []
    for _, p in squad_df.iterrows():
        team    = str(p.get("team", "")).strip()
        exp_pts = round(float(p.get("exp_pts", 0)), 1)
        fix_avg = fixture_difficulty(team, fixtures, groups, next_n)
        label, _ = difficulty_label(fix_avg)

        team_fx = unplayed[
            (unplayed["home_team"].astype(str).str.strip() == team) |
            (unplayed["away_team"].astype(str).str.strip() == team)
        ].head(next_n)

        opponents = []
        for _, fx in team_fx.iterrows():
            opp = fx["away_team"] if str(fx["home_team"]).strip() == team else fx["home_team"]
            opp_rank = get_team_ranking(str(opp).strip(), groups)
            diff_lbl, _ = difficulty_label(opp_rank)
            opponents.append(f"{opp} ({diff_lbl})")

        rows.append({
            "Player":         p.get("name", ""),
            "Pos":            str(p.get("position", "")).upper(),
            "Team":           team if team else "Unknown",
            "Exp pts/g":      exp_pts,
            "Fixtures":       difficulty_label(fix_avg)[0],
            "Next opponents": "  →  ".join(opponents) if opponents else "—",
        })

    return (
        pd.DataFrame(rows)
        .sort_values("Exp pts/g", ascending=False)
        .reset_index(drop=True)
    )


# ── Transfer schedule ─────────────────────────────────────────────────────────

def get_transfer_schedule(
    squad_df: pd.DataFrame,
    all_players: pd.DataFrame,
    fixtures: pd.DataFrame,
    groups: pd.DataFrame,
    transfers_used: int = 0,
    today_str: str = "",
    form: "pd.DataFrame | None" = None,
    actual_stats: "dict | None" = None,
    form_stats: "dict | None" = None,
    recent_form: "set | None" = None,
    advance_probs: "dict | None" = None,
) -> list[dict]:  # noqa: C901
    """
    Returns one entry per matchday ROUND (not per day).

    A matchday round spans ~7 calendar days (e.g. Matchday 1 = June 11–17).
    Within that round, every calendar day with games is a transfer opportunity.

    Strategy rules applied here:
    - Never recommend transferring out a player who plays within 2 days
      (Finnish rule: forfeiting a game's points is almost never worth the gain).
    - Transfer suggestions prioritised by position: FWD → MID → DEF → GK.
    - When picking the best in-player, prefer higher-value options (budget maximisation).

    Each entry:
      round_label         – "Matchday 1", "Round of 32", etc.
      round_start         – first game date in the round (YYYY-MM-DD)
      round_end           – last game date
      round_span_days     – length of round in calendar days
      days_to_start       – calendar days until first game
      urgency             – colour emoji
      suggested_transfers – int (budget recommendation for this round)
      pre_round_swaps     – list of {out, in, position, pts_gain, reason}
      daily_games         – list of {date, days_away, games: [{home, away, squad_home, squad_away}]}
    """
    if fixtures.empty or squad_df.empty:
        return []

    import datetime

    try:
        today = datetime.date.fromisoformat(today_str) if today_str else datetime.date.today()
    except ValueError:
        today = datetime.date.today()

    squad_teams = set(squad_df["team"].astype(str).str.strip().tolist())
    budget_used = squad_budget_used(squad_df)

    unplayed = fixtures.copy()
    if "home_score" in fixtures.columns:
        unplayed = fixtures[
            fixtures["home_score"].isna() | (fixtures["home_score"].astype(str).str.strip() == "")
        ].copy()

    # Teams playing TODAY: strategy rule — almost never transfer these players out.
    # Transfer deadline is before the FIRST game of the day (Eastern Time).
    # So if a player plays today, the window to transfer them out is already closed
    # or about to close — keep them.
    teams_playing_today: set[str] = set()
    teams_playing_tomorrow: set[str] = set()
    for _, row in unplayed.iterrows():
        d = str(row.get("date", "")).strip()
        try:
            gd = datetime.date.fromisoformat(d)
            diff = (gd - today).days
            if diff == 0:
                teams_playing_today.add(str(row.get("home_team", "")).strip())
                teams_playing_today.add(str(row.get("away_team", "")).strip())
            elif diff == 1:
                teams_playing_tomorrow.add(str(row.get("home_team", "")).strip())
                teams_playing_tomorrow.add(str(row.get("away_team", "")).strip())
        except ValueError:
            pass
    # Combined set used to protect players in near-term games
    teams_playing_soon = teams_playing_today | teams_playing_tomorrow

    def _round_key(row) -> str:
        md    = str(row.get("matchday", "")).strip()
        stage = str(row.get("stage", "")).strip()
        if md and md not in ("nan", ""):
            try:
                return f"Matchday {int(float(md))}"
            except ValueError:
                pass
        if stage and stage not in ("Group Stage",):
            return stage
        return ""

    rounds: dict[str, dict] = {}
    for _, row in unplayed.iterrows():
        d = str(row.get("date", "")).strip()
        if not d:
            continue
        try:
            datetime.date.fromisoformat(d)
        except ValueError:
            continue
        key = _round_key(row)
        if not key:
            continue
        if key not in rounds:
            rounds[key] = {"dates_games": {}}
        rounds[key]["dates_games"].setdefault(d, []).append(row)

    if not rounds:
        return []

    # Group stage date range — used to compute knockout_proximity_t per round
    _gs_start, _gs_end = _group_stage_date_range(fixtures)

    transfers_remaining = MAX_TRANSFERS - transfers_used
    n_rounds = len(rounds)
    per_round = max(1, round(transfers_remaining / max(n_rounds, 1)))

    # Pre-score all available players once (expensive — do it here, not per round)
    if "exp_pts" not in all_players.columns:
        all_players = all_players.copy()
        all_players["exp_pts"] = all_players.apply(
            lambda r: expected_matchday_points(r, fixtures, groups, next_n=3, form=form, actual_stats=actual_stats, form_stats=form_stats, recent_form=recent_form), axis=1
        )
    sq_names = set(squad_df["name"].astype(str).str.strip())
    budget_used = squad_budget_used(squad_df)

    # Transfer gain threshold scales with budget pressure.
    # Comfortable (>1.5 per round) → require 2 pts gain; tight (<1.0) → require 4 pts.
    total_future_rounds = sum(
        1 for k in rounds
        if min(rounds[k]["dates_games"].keys()) >= today.isoformat()
    )
    transfer_rate = transfers_remaining / max(total_future_rounds, 1)
    if transfer_rate < 1.0:
        min_gain_threshold = 4.0
    elif transfer_rate < 1.5:
        min_gain_threshold = 3.0
    else:
        min_gain_threshold = 2.0

    # Lower number = transfer out first (strategy: FWD/MID upgrades > DEF > GK)
    _POS_PRIORITY = {"FWD": 0, "MID": 1, "DEF": 2, "GK": 3}

    # Global state carries across rounds so the same player is never suggested twice
    global_running_sq  = sq_names.copy()
    global_running_bud = int(budget_used)
    global_seen_out:  set[str] = set()
    global_seen_in:   set[str] = set()

    schedule = []
    for round_key in sorted(rounds.keys(), key=lambda k: min(rounds[k]["dates_games"].keys())):
        dates_games = rounds[round_key]["dates_games"]
        sorted_dates = sorted(dates_games.keys())
        round_start_str = sorted_dates[0]
        round_end_str   = sorted_dates[-1]

        try:
            round_start = datetime.date.fromisoformat(round_start_str)
            round_end   = datetime.date.fromisoformat(round_end_str)
        except ValueError:
            continue

        days_to_start = (round_start - today).days
        round_span    = (round_end - round_start).days + 1

        # Skip fully past rounds
        if (round_end - today).days < 0:
            continue

        if days_to_start <= 0:
            urgency = "In progress"
        elif days_to_start <= 2:
            urgency = "Starts soon"
        elif days_to_start <= 6:
            urgency = "This week"
        else:
            urgency = "Upcoming"

        # Is this a knockout round?  (round_key contains "Matchday" for group stage)
        _round_is_ko = "matchday" not in round_key.lower()

        # Knockout proximity: 0.0 at tournament start → 1.0 at last group stage day.
        # In knockout rounds the full advance weight always applies (t=1.0).
        if _round_is_ko or _gs_start is None or _gs_end is None or _gs_start == _gs_end:
            knockout_proximity_t = 1.0
        else:
            _t_range = (_gs_end - _gs_start).days
            _t_elapsed = (today - _gs_start).days
            knockout_proximity_t = min(1.0, max(0.0, _t_elapsed / _t_range))

        # ── Day-specific transfer opportunities ───────────────────────────────
        # For each future day in this round:
        #   1. Find which squad players play that day
        #   2. Find best available players who play that day
        #   3. Match them against same-position squad players who DON'T play that day
        #   4. Compare in-player's day_pts vs out-player's day_pts (0 if they don't play)
        # Never transfer out a player who plays the same day as the in-player.

        # Identify coverage gaps: days in this round where no squad player has a game
        uncovered_days: list[str] = []
        for d_str in sorted_dates:
            try:
                datetime.date.fromisoformat(d_str)
            except ValueError:
                continue
            day_fx_chk = unplayed[unplayed["date"].astype(str).str.strip() == d_str]
            teams_chk: set[str] = set()
            for _, fx in day_fx_chk.iterrows():
                teams_chk.add(str(fx.get("home_team", "")).strip())
                teams_chk.add(str(fx.get("away_team", "")).strip())
            if not squad_df["team"].astype(str).str.strip().isin(teams_chk).any():
                uncovered_days.append(d_str)

        # Greedy sequential swap collection — inherits global state so a player
        # already transferred out in a prior round is never suggested again.
        running_budget   = global_running_bud
        running_sq_names = global_running_sq.copy()
        seen_out_r: set[str] = set()
        seen_in_r:  set[str] = set()
        swaps: list[dict] = []

        gap_days     = [d for d in sorted_dates if d in uncovered_days]
        non_gap_days = [d for d in sorted_dates if d not in uncovered_days]
        ordered_days = gap_days + non_gap_days  # gaps processed first

        for d_str in ordered_days:
            if len(swaps) >= max(3, per_round):
                break
            try:
                d_date_obj = datetime.date.fromisoformat(d_str)
            except ValueError:
                continue
            days_until = (d_date_obj - today).days
            if days_until < 0:
                continue

            day_fx = unplayed[unplayed["date"].astype(str).str.strip() == d_str]
            if day_fx.empty:
                continue

            teams_today_r: set[str] = set()
            for _, fx in day_fx.iterrows():
                teams_today_r.add(str(fx.get("home_team", "")).strip())
                teams_today_r.add(str(fx.get("away_team", "")).strip())

            # Use running squad state (reflects prior accepted swaps this round)
            sq_current = squad_df[squad_df["name"].astype(str).str.strip().isin(running_sq_names)]
            sq_not_playing = sq_current[~sq_current["team"].astype(str).str.strip().isin(teams_today_r)]

            if sq_not_playing.empty:
                continue  # everyone plays today — don't break coverage

            # Available players playing today (not already in running squad, not already lined up)
            avail_today = all_players[
                all_players["team"].astype(str).str.strip().isin(teams_today_r) &
                ~all_players["name"].astype(str).str.strip().isin(running_sq_names) &
                ~all_players["name"].astype(str).str.strip().isin(seen_in_r | global_seen_in)
            ].copy()
            if avail_today.empty:
                continue

            avail_today["day_pts"] = avail_today.apply(
                lambda r: _score_for_date(r, day_fx, groups, form, actual_stats=actual_stats, form_stats=form_stats, recent_form=recent_form), axis=1
            )
            avail_today = avail_today[avail_today["day_pts"] > 1.5]
            if avail_today.empty:
                continue

            is_gap = d_str in uncovered_days

            # Team concentration in the current running squad.
            # Hard limit per team:
            #   Knockout rounds → 2  (one loss eliminates the whole cluster)
            #   Late group stage (proximity ≥ 0.7) → 2 for shaky teams, else 3
            #   Early/mid group stage → 3
            _sq_now = squad_df[squad_df["name"].astype(str).str.strip().isin(running_sq_names)]
            _team_counts: dict = (
                _sq_now["team"].astype(str).str.strip()
                .value_counts().to_dict()
            )
            _max_same_team = 2 if _round_is_ko else 3

            for pos in ["FWD", "MID", "DEF", "GK"]:
                if len(swaps) >= max(3, per_round):
                    break

                in_cands  = avail_today[avail_today["position"].str.upper() == pos]
                out_cands = sq_not_playing[
                    (sq_not_playing["position"].str.upper() == pos) &
                    ~sq_not_playing["name"].astype(str).str.strip().isin(seen_out_r | global_seen_out)
                ]

                if in_cands.empty or out_cands.empty:
                    continue

                # Sell-score: low exp_pts + hard fixtures + low advance probability.
                # Advance weight raised to 5.0 so likely-eliminated teams are aggressively
                # flagged as sell candidates.
                if "exp_pts" not in out_cands.columns:
                    out_cands = out_cands.copy()
                    out_cands["exp_pts"] = out_cands.apply(
                        lambda r: expected_matchday_points(r, fixtures, groups, form=form, actual_stats=actual_stats, form_stats=form_stats, recent_form=recent_form), axis=1
                    )
                out_cands = out_cands.copy()
                def _survival_prob(team_str: str) -> float:
                    if _round_is_ko:
                        return compute_ko_win_prob(team_str, fixtures, groups, advance_probs)
                    return float((advance_probs or {}).get(team_str, 0.5))

                # Concentration sell pressure: a nudge, not a veto.
                # Individual performance (-exp_pts) stays the dominant term.
                # Weight scales with phase and how many transfers remain:
                #   group stage, few transfers  → 0.6 per excess player (barely a nudge)
                #   group stage, many transfers → 0.9
                #   KO stage, few transfers     → 1.2
                #   KO stage, many transfers    → 2.0  (afford to rebalance now)
                _conc_phase   = 1.5 if _round_is_ko else 0.7
                _conc_tx_mult = min(1.35, max(0.85, transfer_rate / 1.5))
                _conc_weight  = _conc_phase * _conc_tx_mult

                out_cands["sell_score"] = out_cands.apply(
                    lambda r: (
                        -float(r.get("exp_pts", 0))
                        + max(0.0, (50.0 - fixture_difficulty(str(r.get("team", "")), fixtures, groups, next_n=2)) / 50.0) * 2.0
                        + max(0.0, 1.0 - _survival_prob(str(r.get("team", "")).strip())) * 5.0
                        # Concentration nudge — only kicks in above the per-phase limit
                        + max(0.0, _team_counts.get(str(r.get("team", "")).strip(), 0) - _max_same_team) * _conc_weight
                    ),
                    axis=1,
                )
                worst_out = out_cands.nlargest(1, "sell_score").iloc[0]

                out_val_freed = parse_value(worst_out.get("value", 0))
                budget_slack_r = BUDGET - running_budget + out_val_freed
                in_cands_ok = in_cands[in_cands["value"].apply(parse_value) <= budget_slack_r].copy()
                if in_cands_ok.empty:
                    continue

                # Concentration hard filter for IN candidates.
                # Hard limits (adding one more would exceed):
                #   KO stage: 2 — one elimination wipes the cluster
                #   Late group (proximity ≥ 0.7) + shaky team (<60% adv): 2
                #   Group stage otherwise: 4 (truly extreme; 3 is soft, not hard)
                def _hard_limit(team_str: str) -> int:
                    if _round_is_ko:
                        return 2
                    if knockout_proximity_t >= 0.7:
                        t_adv = float((advance_probs or {}).get(team_str, 0.5))
                        if t_adv < 0.60:
                            return 2
                    return 4   # group stage soft ceiling — never a 5th from same team

                in_cands_ok = in_cands_ok[
                    in_cands_ok["team"].astype(str).str.strip().map(
                        lambda t: _team_counts.get(t, 0) < _hard_limit(t)
                    )
                ]
                if in_cands_ok.empty:
                    continue

                # Dead rubber filter: if a team is already eliminated (advance_prob == 0)
                # their remaining group fixtures involve heavy rotation and are worthless.
                # Only apply in group stage — knockout eliminates speak for themselves.
                if not _round_is_ko:
                    in_cands_ok = in_cands_ok[
                        in_cands_ok["team"].astype(str).str.strip().map(
                            lambda t: float((advance_probs or {}).get(t, 0.5)) > 0.0
                        )
                    ]
                    if in_cands_ok.empty:
                        continue

                # Score IN candidates combining day_pts with tournament survival probability.
                # Gap days: coverage is the priority — use raw pts only.
                # Group stage: advance weight scales with knockout_proximity_t so early
                #   buys are judged mainly on pts, late buys heavily penalise shaky teams.
                # Knockout rounds: use match win probability (draw→extra time→50/50),
                #   and give GK/DEF a 1.15× boost (fewer goals → clean sheets matter more).
                #
                # All paths also consider remaining fixture count as a tie-breaker:
                # a team with 3 upcoming games is more valuable than one with 1 even at
                # the same expected pts per game.
                def _remaining_games(team_str: str) -> int:
                    return int(unplayed[
                        (unplayed["home_team"].astype(str).str.strip() == team_str) |
                        (unplayed["away_team"].astype(str).str.strip() == team_str)
                    ].shape[0])

                if is_gap:
                    in_cands_ok["_in_adv"]   = 0.5
                    in_cands_ok["_in_score"]  = in_cands_ok["day_pts"]
                elif _round_is_ko:
                    in_cands_ok["_in_adv"] = in_cands_ok["team"].astype(str).str.strip().map(
                        lambda t: compute_ko_win_prob(t, fixtures, groups, advance_probs)
                    )
                    # GK/DEF boost: knockout games are tighter, CS premium increases
                    in_cands_ok["_ko_boost"] = in_cands_ok["position"].str.upper().map(
                        lambda p: 1.15 if p in ("GK", "DEF") else 1.0
                    )
                    # Remaining games bonus: each additional game adds 3% to final score
                    in_cands_ok["_rem_games"] = in_cands_ok["team"].astype(str).str.strip().map(
                        lambda t: _remaining_games(t)
                    )
                    in_cands_ok["_in_score"] = (
                        in_cands_ok["day_pts"] * in_cands_ok["_ko_boost"]
                        * (0.55 + 0.45 * in_cands_ok["_in_adv"])
                        * (1.0 + 0.03 * (in_cands_ok["_rem_games"] - 1).clip(lower=0))
                    )
                else:
                    # Group stage: advance weight grows linearly from 0.20 (day 1) to 0.45 (last MD)
                    in_cands_ok["_in_adv"] = in_cands_ok["team"].astype(str).str.strip().map(
                        lambda t: float((advance_probs or {}).get(t, 0.5))
                    )
                    _base_w = 0.80 - 0.25 * knockout_proximity_t   # 0.80 → 0.55
                    _adv_w  = 1.0 - _base_w                         # 0.20 → 0.45
                    # Remaining games: small bonus so a team with 2 group games beats
                    # an equally-rated team with 1 (everything else equal)
                    in_cands_ok["_rem_games"] = in_cands_ok["team"].astype(str).str.strip().map(
                        lambda t: _remaining_games(t)
                    )
                    in_cands_ok["_in_score"] = (
                        in_cands_ok["day_pts"] * (_base_w + _adv_w * in_cands_ok["_in_adv"])
                        * (1.0 + 0.02 * (in_cands_ok["_rem_games"] - 1).clip(lower=0))
                    )

                best_in = in_cands_ok.nlargest(1, "_in_score").iloc[0]
                day_pts_in = float(best_in["day_pts"])

                # Deferred-buy check: if the IN player has a significantly better fixture
                # within 8 days, skip this date — the schedule will surface them for
                # that better window instead.  Don't waste a transfer slot on an Egypt FWD
                # vs Belgium (2.9 pts) when they play New Zealand in 6 days (5.4 pts).
                # Gap days always proceed — any coverage beats a gap.
                if not is_gap:
                    _in_team = str(best_in.get("team", "")).strip()
                    _deferred = False
                    for _fut_d in sorted(unplayed["date"].astype(str).str.strip().unique()):
                        if _fut_d <= d_str:
                            continue
                        try:
                            _fut_obj = datetime.date.fromisoformat(_fut_d)
                        except ValueError:
                            continue
                        if (_fut_obj - d_date_obj).days > 8:
                            break
                        _fut_fx = unplayed[unplayed["date"].astype(str).str.strip() == _fut_d]
                        _fut_teams = (
                            set(_fut_fx["home_team"].astype(str).str.strip()) |
                            set(_fut_fx["away_team"].astype(str).str.strip())
                        )
                        if _in_team not in _fut_teams:
                            continue
                        _fut_pts = _score_for_date(
                            best_in, _fut_fx, groups, form,
                            actual_stats=actual_stats, form_stats=form_stats, recent_form=recent_form,
                        )
                        if _fut_pts > day_pts_in * 1.5:
                            _deferred = True
                            break
                    if _deferred:
                        continue

                # Gain threshold: scales with transfers remaining.
                # Gap days bypass — any coverage beats none.
                if day_pts_in < min_gain_threshold and not is_gap:
                    continue

                # Extra barrier for risky buys.
                # The survival-probability cutoff rises as we approach / enter knockouts:
                #   early group stage → 0.45 (only block near-certain eliminations)
                #   late group stage  → 0.65 (block borderline teams)
                #   knockout rounds   → 0.65 (use KO win prob — don't buy a team likely
                #                            to lose their very next match)
                in_adv_prelim = float(best_in.get("_in_adv", 0.5))
                _risky_cutoff = 0.45 + 0.20 * knockout_proximity_t   # 0.45 → 0.65
                if not is_gap and in_adv_prelim < _risky_cutoff:
                    extra_factor = 1.0 + (_risky_cutoff - in_adv_prelim) * 0.8
                    if _round_is_ko:
                        extra_factor = max(extra_factor, 1.6)  # knockout buys need clear value
                    if day_pts_in < min_gain_threshold * extra_factor:
                        continue

                out_avg = float(worst_out.get("exp_pts", 0))
                pts_gain = round(day_pts_in - out_avg, 1)

                in_val  = parse_value(best_in.get("value", 0))
                out_val_disp = parse_value(worst_out.get("value", 0))
                value_note = f"  ·  ↑ {in_val/1000:.0f}k" if in_val > out_val_disp + 50_000 else ""

                out_team_str  = str(worst_out.get("team", "")).strip()
                in_team_str   = str(best_in.get("team", ""))
                near_diff     = fixture_difficulty(out_team_str, fixtures, groups, next_n=2)
                late_diff     = fixture_difficulty(out_team_str, fixtures, groups, next_n=6)
                can_buy_back  = bool(late_diff > near_diff + 15)

                # In knockout rounds use KO win prob; in group stage use advance prob
                if _round_is_ko:
                    in_adv  = compute_ko_win_prob(in_team_str, fixtures, groups, advance_probs)
                    out_adv = compute_ko_win_prob(out_team_str, fixtures, groups, advance_probs)
                    _adv_label = "ko win prob"
                    _sell_label = "sell before next round"
                else:
                    in_adv  = float((advance_probs or {}).get(in_team_str, 0.5))
                    out_adv = float((advance_probs or {}).get(out_team_str, 0.5))
                    _adv_label = "to advance"
                    _sell_label = "sell before knockouts"

                # Short-term cutoff rises from 0.45 early in group stage to 0.65 at knockouts
                _short_term_cutoff = 0.45 + 0.20 * knockout_proximity_t
                is_short_term = (in_adv < _short_term_cutoff) and not is_gap

                adv_note = ""
                sell_intent_note = ""
                _warn_thresh = _short_term_cutoff - 0.15   # hard-warn below this
                if in_adv < _warn_thresh:
                    adv_note = f"  ·  ⚠ {in_team_str} only {in_adv:.0%} {_adv_label}"
                elif is_short_term:
                    adv_note = f"  ·  {in_team_str} {in_adv:.0%} {_adv_label}"
                elif out_adv < _warn_thresh and out_adv > 0.0:
                    adv_note = f"  ·  sell: {out_team_str} only {out_adv:.0%} {_adv_label}"

                if is_short_term:
                    sell_intent_note = f"  ·  SHORT-TERM BUY — {_sell_label}"

                # Concentration note: warn when this buy creates a 2-player cluster
                # (which is fine but worth knowing) or when selling reduces an over-cluster
                _in_count_after  = _team_counts.get(in_team_str, 0) + 1
                _out_count_before = _team_counts.get(out_team_str, 0)
                conc_note = ""
                if _in_count_after >= 2:
                    conc_note = f"  ·  {_in_count_after}× {in_team_str} in squad"
                if _out_count_before > _max_same_team:
                    conc_note += f"  ·  reduces {out_team_str} cluster"

                swap = {
                    "transfer_date":   d_str,
                    "days_until":      days_until,
                    "out":             display_name(str(worst_out["name"])),
                    "out_team":        out_team_str,
                    "in":              display_name(str(best_in["name"])),
                    "in_team":         in_team_str,
                    "position":        pos,
                    "day_pts":         round(day_pts_in, 1),
                    "pts_gain":        pts_gain,
                    "is_gap_day":      is_gap,
                    "can_buy_back":    can_buy_back,
                    "in_advance_prob": round(in_adv, 2),
                    "is_short_term":   is_short_term,
                    "is_ko_round":     _round_is_ko,
                    "reason":          (
                        f"Plays {d_str}  ·  ~{day_pts_in:.1f} pts today  ·  "
                        f"out-player avg {out_avg:.1f}{value_note}{adv_note}{sell_intent_note}{conc_note}"
                        + ("  ·  Rebuy candidate" if can_buy_back else "")
                    ),
                }

                swaps.append(swap)
                seen_out_r.add(str(worst_out["name"]).strip())
                seen_in_r.add(str(best_in["name"]).strip())

                # Chain update: reflect this swap's budget and squad impact on subsequent iterations
                running_budget += int(in_val) - int(out_val_freed)
                running_sq_names.discard(str(worst_out["name"]).strip())
                running_sq_names.add(str(best_in["name"]).strip())

        # Restore chronological order for the UI (gap days were front-loaded for priority)
        swaps.sort(key=lambda x: x["transfer_date"])

        # Propagate this round's accepted swaps into global state for next rounds
        for _sw in swaps:
            global_seen_out.add(_sw["out"])
            global_seen_in.add(_sw["in"])
        global_running_sq.clear()
        global_running_sq.update(running_sq_names)
        global_running_bud = running_budget

        # Day-by-day breakdown with squad markers
        daily_games = []
        for d_str in sorted_dates:
            try:
                d_date = datetime.date.fromisoformat(d_str)
            except ValueError:
                continue
            days_away_d = (d_date - today).days
            games = []
            for _, fx in pd.DataFrame(dates_games[d_str]).iterrows():
                home = str(fx.get("home_team", ""))
                away = str(fx.get("away_team", ""))
                games.append({
                    "home":       home,
                    "away":       away,
                    "squad_home": home in squad_teams,
                    "squad_away": away in squad_teams,
                })
            daily_games.append({
                "date":      d_str,
                "days_away": days_away_d,
                "games":     games,
            })

        schedule.append({
            "round_label":         round_key,
            "round_start":         round_start_str,
            "round_end":           round_end_str,
            "round_span_days":     round_span,
            "days_to_start":       days_to_start,
            "urgency":             urgency,
            "suggested_transfers": per_round,
            "uncovered_days":      uncovered_days,
            "pre_round_swaps":     swaps,
            "daily_games":         daily_games,
        })

    return schedule


# ── Legacy helpers (kept for compatibility) ───────────────────────────────────

def squad_budget_used(squad: pd.DataFrame) -> float:
    if squad.empty or "value" not in squad.columns:
        return 0.0
    return sum(parse_value(v) for v in squad["value"])
