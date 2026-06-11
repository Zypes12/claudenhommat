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
"""
from __future__ import annotations
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
    """Lowercase, strip accents, collapse spaces for fuzzy matching."""
    nfkd = unicodedata.normalize("NFKD", str(s))
    ascii_s = "".join(c for c in nfkd if not unicodedata.combining(c))
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

    def find_team(player_name: str) -> str:
        norm_player = _norm(player_name)
        parts = norm_player.split()

        # 1. Exact full-name match
        if norm_player in lineup_map:
            return lineup_map[norm_player]

        # 2. Last name only (players.csv stores "LastName FirstName")
        #    This is the reliable path — avoids matching first names like "David"
        #    against unrelated players (e.g. Jonathan David for Canada).
        if parts and parts[0] in lineup_map:
            return lineup_map[parts[0]]

        # 3. Two-word compound last name (e.g. "De Bruyne", "Van Dijk")
        if len(parts) >= 2:
            compound = parts[0] + " " + parts[1]
            if compound in lineup_map:
                return lineup_map[compound]

        return ""

    df["team"] = df["name"].astype(str).apply(find_team)
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


def _result_probs(team_rank: float, opp_rank: float) -> tuple[float, float, float]:
    rank_diff = opp_rank - team_rank
    raw_win = 1 / (1 + math.exp(-rank_diff * 0.04))
    draw = 0.22
    win = min(max(raw_win - draw / 2, 0.05), 0.85)
    loss = max(1.0 - win - draw, 0.05)
    return win, draw, loss


def fixture_difficulty(team: str, fixtures: pd.DataFrame, groups: pd.DataFrame, next_n: int = 3) -> float:
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


# ── Expected points estimation ─────────────────────────────────────────────────

def expected_matchday_points(
    player: pd.Series,
    fixtures: pd.DataFrame,
    groups: pd.DataFrame,
    next_n: int = 3,
) -> float:
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
        # No team data — neutral base estimate
        return round(2.5 + (1.0 if pos in ("GK", "DEF") else 0.5), 2)

    total_pts = 0.0
    for _, row in team_fx.iterrows():
        opp = row["away_team"] if str(row["home_team"]).strip() == team else row["home_team"]
        team_rank = get_team_ranking(team, groups)
        opp_rank  = get_team_ranking(str(opp).strip(), groups)
        win_p, draw_p, loss_p = _result_probs(team_rank, opp_rank)

        pts = 2.0  # appearance + 60 min
        pts += win_p * pp["win"] + draw_p * pp["draw"] + loss_p * pp["loss"]

        cs_prob = win_p * 0.60 + draw_p * 0.20
        pts += cs_prob * pp["clean_sheet"]

        if pos == "GK":
            pts += 1.5

        if pos == "DEF":
            expected_ga = loss_p * 2.5 + draw_p * 0.5
            if expected_ga < 2:
                pts -= 0.5
            elif expected_ga < 4:
                pts -= 1.0
            else:
                pts -= 1.5

        is_pen_taker = str(player.get("penalty_taker", "")).strip().lower() in ("primary", "secondary")
        pen_primary  = str(player.get("penalty_taker", "")).strip().lower() == "primary"
        if is_pen_taker:
            pen_rate = 0.4 if pen_primary else 0.2
            pts += pen_rate * (0.80 * (pp["goal"] - 2) + 0.20 * -2)

        spr = str(player.get("set_piece_role", "")).strip().lower()
        if spr in ("both", "free kicks"):
            pts += 0.3 * pp["assist"]
        if spr in ("both", "corners"):
            pts += 0.2 * pp["assist"]

        if pos in ("MID", "FWD"):
            pts += (opp_rank / 100) * 0.5

        total_pts += pts

    return round(total_pts / len(team_fx), 2)


# ── Squad optimizer ────────────────────────────────────────────────────────────

MIN_PLAYER_VALUE = 275_000  # used for budget reservation


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
        # Reserve minimum value for remaining positions
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
) -> dict | None:
    """
    Returns the optimal 11-player squad within budget.

    Result keys:
      squad        – DataFrame with exp_pts column
      formation    – e.g. "4-3-3"
      captain      – player name string
      captain_pts  – expected pts as captain
      total_pts    – squad total expected pts/game
      budget_used  – int euros
    """
    if players.empty:
        return None

    enriched = _enrich_with_team(players, lineups)
    enriched = enriched.copy()
    enriched["exp_pts"] = enriched.apply(
        lambda r: expected_matchday_points(r, fixtures, groups), axis=1
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
    best["enriched_players"] = enriched  # expose for transfer page
    return best


# ── Transfer suggestions ───────────────────────────────────────────────────────

def recommend_transfers(
    squad_df: pd.DataFrame,
    all_players: pd.DataFrame,
    fixtures: pd.DataFrame,
    groups: pd.DataFrame,
    n_suggestions: int = 5,
    position_filter: str | None = None,
) -> dict:
    """
    Given current squad, suggest transfers in and out.
    Returns {"out": [...], "in": [...]}
    """
    if squad_df.empty or all_players.empty:
        return {"out": [], "in": []}

    squad_names = set(squad_df["name"].astype(str).str.strip().tolist())
    available = all_players[~all_players["name"].astype(str).str.strip().isin(squad_names)].copy()

    if position_filter:
        available = available[available["position"].str.upper() == position_filter.upper()]
        squad_pos = squad_df[squad_df["position"].str.upper() == position_filter.upper()].copy()
    else:
        squad_pos = squad_df.copy()

    # Score available players
    if "exp_pts" not in available.columns:
        available["exp_pts"] = available.apply(
            lambda r: expected_matchday_points(r, fixtures, groups), axis=1
        )
    if "exp_pts" not in squad_pos.columns:
        squad_pos["exp_pts"] = squad_pos.apply(
            lambda r: expected_matchday_points(r, fixtures, groups), axis=1
        )

    top_in = available.nlargest(n_suggestions, "exp_pts")
    worst_out = squad_pos.nsmallest(n_suggestions, "exp_pts")

    def row_to_dict(row, tag=""):
        pt = str(row.get("penalty_taker", "")).lower()
        spr = str(row.get("set_piece_role", "")).lower()
        reasons = []
        if pt in ("primary", "secondary"):
            reasons.append(f"Penalty taker ({pt})")
        if spr not in ("no", "none", ""):
            reasons.append(f"Set pieces: {spr}")
        team = str(row.get("team", ""))
        if team:
            diff = fixture_difficulty(team, fixtures, groups)
            label, _ = difficulty_label(diff)
            reasons.append(f"{label} fixtures (avg rank {diff:.0f})")
        return {
            "name": row.get("name", ""),
            "position": str(row.get("position", "")).upper(),
            "value": row.get("value", "?"),
            "exp_pts": round(float(row.get("exp_pts", 0)), 1),
            "team": team,
            "reason": "  ·  ".join(reasons) if reasons else "—",
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
        team = str(p.get("team", "")).strip()
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
            "Player":      p.get("name", ""),
            "Pos":         str(p.get("position", "")).upper(),
            "Team":        team if team else "Unknown",
            "Exp pts/g":   exp_pts,
            "Fixtures":    difficulty_label(fix_avg)[0],
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
) -> list[dict]:  # noqa: C901
    """
    Returns one entry per matchday ROUND (not per day).

    A matchday round spans ~7 calendar days (e.g. Matchday 1 = June 11–17).
    Within that round, every calendar day with games is a transfer opportunity.

    Each entry:
      round_label   – "Matchday 1", "Round of 32", etc.
      round_start   – first game date in the round (YYYY-MM-DD)
      round_end     – last game date in the round
      days_to_start – calendar days until first game
      urgency       – colour emoji
      suggested_transfers – int (budget recommendation for this round)
      pre_round_swaps – list of {out, in, position, pts_gain, reason}
      daily_games   – list of {date, days_away, games: [{home, away, squad_home, squad_away}]}
    """
    if fixtures.empty or squad_df.empty:
        return []

    import datetime

    try:
        today = datetime.date.fromisoformat(today_str) if today_str else datetime.date.today()
    except ValueError:
        today = datetime.date.today()

    squad_teams = set(squad_df["team"].astype(str).str.strip().tolist())

    unplayed = fixtures.copy()
    if "home_score" in fixtures.columns:
        unplayed = fixtures[
            fixtures["home_score"].isna() | (fixtures["home_score"].astype(str).str.strip() == "")
        ].copy()

    # ── Build round → {date → [row]} map ──────────────────────────────────────
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
            game_date = datetime.date.fromisoformat(d)
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

    transfers_remaining = MAX_TRANSFERS - transfers_used
    n_rounds = len(rounds)
    # Front-load transfers: more for early rounds (fixture data is better)
    # Simple even split; caller can adjust
    per_round = max(1, round(transfers_remaining / max(n_rounds, 1)))

    # ── Pre-score all available players once ──────────────────────────────────
    if "exp_pts" not in all_players.columns:
        all_players = all_players.copy()
        all_players["exp_pts"] = all_players.apply(
            lambda r: expected_matchday_points(r, fixtures, groups, next_n=3), axis=1
        )
    sq_names = set(squad_df["name"].astype(str).str.strip())

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
            urgency = "🔴 In progress"
        elif days_to_start <= 2:
            urgency = "🟠 Starts soon"
        elif days_to_start <= 6:
            urgency = "🟡 This week"
        else:
            urgency = "🟢 Upcoming"

        # ── Pre-round swap recommendations ────────────────────────────────────
        # Restrict fixtures to just this round for scoring
        round_fixture_dates = set(sorted_dates)
        round_fx = unplayed[unplayed["date"].astype(str).str.strip().isin(round_fixture_dates)]

        sq = squad_df.copy()
        sq["round_pts"] = sq.apply(
            lambda r: expected_matchday_points(r, round_fx if not round_fx.empty else fixtures, groups, next_n=len(sorted_dates)),
            axis=1,
        )
        worst = sq.nsmallest(min(3, per_round), "round_pts")

        swaps = []
        for _, out_row in worst.iterrows():
            pos = str(out_row.get("position", "")).upper()
            candidates = all_players[
                (~all_players["name"].astype(str).str.strip().isin(sq_names)) &
                (all_players["position"].str.upper() == pos)
            ]
            if candidates.empty:
                continue
            best_in = candidates.nlargest(1, "exp_pts").iloc[0]
            gain = round(float(best_in["exp_pts"]) - float(out_row.get("round_pts", 0)), 1)
            if gain > 0.2:
                team_in = str(best_in.get("team", ""))
                diff = fixture_difficulty(team_in, fixtures, groups, next_n=3)
                lbl, _ = difficulty_label(diff)
                swaps.append({
                    "out":      display_name(str(out_row["name"])),
                    "in":       display_name(str(best_in["name"])),
                    "position": pos,
                    "pts_gain": gain,
                    "reason":   f"{lbl} fixtures · +{gain:.1f} pts/g vs. current pick",
                })

        # ── Daily game breakdown ───────────────────────────────────────────────
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
                    "home":        home,
                    "away":        away,
                    "squad_home":  home in squad_teams,
                    "squad_away":  away in squad_teams,
                })
            daily_games.append({
                "date":      d_str,
                "days_away": days_away_d,
                "games":     games,
            })

        schedule.append({
            "round_label":          round_key,
            "round_start":          round_start_str,
            "round_end":            round_end_str,
            "round_span_days":      round_span,
            "days_to_start":        days_to_start,
            "urgency":              urgency,
            "suggested_transfers":  per_round,
            "pre_round_swaps":      swaps,
            "daily_games":          daily_games,
        })

    return schedule


# ── Legacy helpers (kept for compatibility) ───────────────────────────────────

def squad_budget_used(squad: pd.DataFrame) -> float:
    if squad.empty or "value" not in squad.columns:
        return 0.0
    return sum(parse_value(v) for v in squad["value"])
