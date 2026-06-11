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
import pandas as pd


# ── Scoring tables ─────────────────────────────────────────────────────────────

POSITION_POINTS: dict[str, dict] = {
    "GK":  {"goal": 9, "assist": 6, "win": 3, "draw": 1, "loss": -2, "clean_sheet": 3},
    "DEF": {"goal": 7, "assist": 4, "win": 2, "draw": 1, "loss": -1, "clean_sheet": 2},
    "MID": {"goal": 5, "assist": 3, "win": 1, "draw": 0, "loss":  0, "clean_sheet": 1},
    "FWD": {"goal": 4, "assist": 2, "win": 0, "draw": 0, "loss":  0, "clean_sheet": 0},
}
CAPTAIN_MULTIPLIER = 1.3
BUDGET = 3_800_000
SQUAD_SIZE = 11
VALID_FORMATIONS = ["4-4-2", "4-3-3", "4-5-1", "3-5-2", "3-4-3", "5-3-2", "5-4-1"]


# ── Squad helpers ──────────────────────────────────────────────────────────────

def get_squad(players: pd.DataFrame) -> pd.DataFrame:
    """Rows from players.csv where in_squad is True."""
    if players.empty or "in_squad" not in players.columns:
        return pd.DataFrame(columns=players.columns if not players.empty else [])
    mask = players["in_squad"].astype(str).str.strip().str.lower().isin(["true", "1", "yes"])
    return players[mask].copy().reset_index(drop=True)


def squad_budget_used(squad: pd.DataFrame) -> float:
    """Sum of squad player values (strips '€' and spaces, returns float)."""
    if squad.empty or "value" not in squad.columns:
        return 0.0
    total = 0.0
    for v in squad["value"]:
        try:
            total += float(str(v).replace("€", "").replace("€", "").replace(" ", "").replace("\xa0", ""))
        except ValueError:
            pass
    return total


# ── Fixture / ranking helpers ──────────────────────────────────────────────────

def get_team_ranking(team: str, groups: pd.DataFrame) -> float:
    """FIFA ranking from groups.csv. Lower = stronger."""
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
    """
    Rough win/draw/loss probabilities based on FIFA ranking difference.
    Higher rank number = weaker team.
    """
    rank_diff = opp_rank - team_rank   # positive → facing weaker opponent
    raw_win = 1 / (1 + math.exp(-rank_diff * 0.04))
    draw = 0.22
    win = min(max(raw_win - draw / 2, 0.05), 0.85)
    loss = max(1.0 - win - draw, 0.05)
    return win, draw, loss


def fixture_difficulty(team: str, fixtures: pd.DataFrame, groups: pd.DataFrame, next_n: int = 3) -> float:
    """
    Score where higher = easier upcoming fixtures.
    Returns average opponent FIFA ranking across next_n unplayed fixtures.
    """
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
        opp = row["away_team"] if row["home_team"].strip() == team.strip() else row["home_team"]
        scores.append(get_team_ranking(str(opp).strip(), groups))

    return round(sum(scores) / len(scores), 1)


# ── Expected points estimation ─────────────────────────────────────────────────

def expected_matchday_points(
    player: pd.Series,
    fixtures: pd.DataFrame,
    groups: pd.DataFrame,
    next_n: int = 3,
) -> float:
    """
    Estimate a player's expected points per matchday, using game rules and
    fixture difficulty. Used to rank/compare players objectively.

    Factors used:
      - Win/draw/loss probability (from FIFA ranking gap)
      - Clean sheet probability (roughly correlated with result)
      - Penalty taker bonus
      - Set piece role (extra assist probability)
      - Position-specific scoring weights
    """
    pos = str(player.get("position", "")).strip().upper()
    if pos not in POSITION_POINTS:
        pos = "MID"  # safe default

    pp = POSITION_POINTS[pos]
    team = str(player.get("team", "")).strip() if pd.notna(player.get("team")) else ""

    # --- Get upcoming fixtures for this team ---
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
        # No fixtures data — use a neutral estimate
        base = 2.0 + (2.0 if pos in ("GK", "DEF") else 1.0)
        return base

    total_pts = 0.0
    num_fixtures = len(team_fx)

    for _, row in team_fx.iterrows():
        opp = row["away_team"] if str(row["home_team"]).strip() == team else row["home_team"]
        team_rank = get_team_ranking(team, groups)
        opp_rank  = get_team_ranking(str(opp).strip(), groups)

        win_p, draw_p, loss_p = _result_probs(team_rank, opp_rank)

        # --- Appearance + 60-min points (assume regular starter if in lineup) ---
        pts = 2.0  # appearance (1) + 60-min (1)

        # --- Result bonus ---
        pts += win_p * pp["win"] + draw_p * pp["draw"] + loss_p * pp["loss"]

        # --- Clean sheet probability (correlated with win/draw against weaker side) ---
        # Rough heuristic: CS prob ≈ 60% of win prob + 20% of draw prob
        cs_prob = win_p * 0.60 + draw_p * 0.20
        pts += cs_prob * pp["clean_sheet"]

        # --- GK saves bonus: facing more shots from weaker teams unlikely;
        #     facing stronger teams → more shots but risk of loss/concede
        #     Use a flat 1.5 expected save-pts for active GKs (real data needed) ---
        if pos == "GK":
            pts += 1.5  # rough average save bonus

        # --- Goals-against penalty for DEF ---
        if pos == "DEF":
            # Expected goals against correlates with loss probability
            expected_ga = loss_p * 2.5 + draw_p * 0.5
            if expected_ga < 2:
                pts -= 0.5
            elif expected_ga < 4:
                pts -= 1.0
            else:
                pts -= 1.5

        # --- Penalty taker ---
        is_pen_taker = str(player.get("penalty_taker", "")).strip().lower() in ("primary", "secondary")
        pen_primary  = str(player.get("penalty_taker", "")).strip().lower() == "primary"
        if is_pen_taker:
            # Rough: ~0.4 penalties per game for primary taker on good team
            pen_rate = 0.4 if pen_primary else 0.2
            pen_score_pts = pp["goal"] - 2  # goal points minus missed_pen cost if saved
            # Scored ~80% of pens, missed ~20%
            pts += pen_rate * (0.80 * pen_score_pts + 0.20 * -2)

        # --- Set piece role: extra assist probability ---
        spr = str(player.get("set_piece_role", "")).strip().lower()
        if spr in ("both", "free kicks"):
            pts += 0.3 * pp["assist"]   # rough free-kick assist chance
        if spr in ("both", "corners"):
            pts += 0.2 * pp["assist"]   # rough corner assist chance

        # --- Shots on target (attacking players) ---
        if pos in ("MID", "FWD"):
            # Attacking players from teams facing weak sides → more shots
            # Rough: 0.5 pts expected shots bonus
            pts += (opp_rank / 100) * 0.5

        total_pts += pts

    return round(total_pts / num_fixtures, 2)


# ── Captain recommendation ─────────────────────────────────────────────────────

def recommend_captain(players: pd.DataFrame, fixtures: pd.DataFrame, groups: pd.DataFrame) -> dict:
    """
    Returns the recommended captain.
    Best captain = player with highest expected points × 1.3 multiplier.
    Prioritises penalty takers from teams with easy fixtures.
    """
    squad = get_squad(players)
    if squad.empty:
        return {"player": None, "reason": "No squad set — tick in_squad for your players on Data Input."}

    squad = squad.copy()
    squad["exp_pts"] = squad.apply(
        lambda r: expected_matchday_points(r, fixtures, groups), axis=1
    )
    squad["cap_pts"] = squad["exp_pts"] * CAPTAIN_MULTIPLIER

    best_idx = squad["cap_pts"].idxmax()
    best = squad.loc[best_idx]

    reasons = []
    pos = str(best.get("position", "")).upper()
    reasons.append(f"expected ~{best['exp_pts']:.1f} pts/game → {best['cap_pts']:.1f} as captain (×1.3)")

    pt = str(best.get("penalty_taker", "")).lower()
    if pt in ("primary", "secondary"):
        reasons.append(f"penalty taker ({pt})")

    spr = str(best.get("set_piece_role", "")).lower()
    if spr not in ("no", "none", ""):
        reasons.append(f"set pieces: {spr}")

    fix_score = fixture_difficulty(str(best.get("team", "")), fixtures, groups)
    reasons.append(f"fixture score {fix_score} (higher = easier opponents)")

    return {
        "player": best["name"],
        "position": pos,
        "expected_pts": round(best["exp_pts"], 1),
        "captain_pts": round(best["cap_pts"], 1),
        "reason": " | ".join(reasons),
    }


# ── Transfer suggestions ───────────────────────────────────────────────────────

def recommend_transfers(
    players: pd.DataFrame,
    fixtures: pd.DataFrame,
    groups: pd.DataFrame,
    lineups: pd.DataFrame,
    n_suggestions: int = 3,
    position_filter: str | None = None,
) -> list[dict]:
    """
    Suggest players to bring in, ranked by expected matchday points.
    Transfers are precious (max 50 total tournament) — only suggest
    players with meaningfully better expected score than squad players.
    """
    squad = get_squad(players)
    if players.empty:
        return []

    squad_names = set(squad["name"].astype(str).str.strip().tolist())
    available = players[~players["name"].astype(str).str.strip().isin(squad_names)].copy()

    if position_filter:
        available = available[available["position"].str.upper() == position_filter.upper()]

    if available.empty:
        return []

    # Exclude players who were recently benched/dropped
    if not lineups.empty and "status" in lineups.columns:
        try:
            latest_md = lineups["matchday"].max()
            inactive = set(
                lineups[
                    (lineups["matchday"] == latest_md) &
                    (lineups["status"].isin(["benched", "not_in_squad"]))
                ]["player_name"].astype(str).str.strip().tolist()
            )
            available = available[~available["name"].astype(str).str.strip().isin(inactive)]
        except Exception:
            pass

    available = available.copy()
    available["exp_pts"] = available.apply(
        lambda r: expected_matchday_points(r, fixtures, groups), axis=1
    )
    top = available.nlargest(n_suggestions, "exp_pts")

    suggestions = []
    for _, row in top.iterrows():
        reasons = [f"expected ~{row['exp_pts']:.1f} pts/game"]
        pt = str(row.get("penalty_taker", "")).lower()
        if pt in ("primary", "secondary"):
            reasons.append(f"penalty taker ({pt})")
        spr = str(row.get("set_piece_role", "")).lower()
        if spr not in ("no", "none", ""):
            reasons.append(f"set pieces: {spr}")
        fix = fixture_difficulty(str(row.get("team", "")), fixtures, groups)
        reasons.append(f"fixture score {fix}")
        suggestions.append({
            "name": row["name"],
            "position": row.get("position", ""),
            "value": row.get("value", "?"),
            "exp_pts": row["exp_pts"],
            "reason": " | ".join(reasons),
        })

    return suggestions


# ── Squad overview / fixture difficulty ───────────────────────────────────────

def squad_fixture_summary(
    players: pd.DataFrame,
    fixtures: pd.DataFrame,
    groups: pd.DataFrame,
) -> pd.DataFrame:
    """Full fixture + expected-points breakdown for every player in the squad."""
    squad = get_squad(players)
    if squad.empty:
        return pd.DataFrame()

    rows = []
    for _, p in squad.iterrows():
        team = str(p.get("team", "")) if pd.notna(p.get("team")) else ""
        fix_score = fixture_difficulty(team, fixtures, groups)
        exp_pts   = expected_matchday_points(p, fixtures, groups)
        is_cap = str(p.get("is_captain", "")).lower() in ("true", "1", "yes")
        rows.append({
            "Player":          p.get("name", ""),
            "Pos":             p.get("position", ""),
            "Fixture score":   fix_score,
            "Exp pts/game":    exp_pts,
            "Cap pts/game":    round(exp_pts * CAPTAIN_MULTIPLIER, 1) if is_cap else "",
            "Pen taker":       p.get("penalty_taker", "No"),
            "Set pieces":      p.get("set_piece_role", "No"),
            "Captain":         "✓" if is_cap else "",
        })

    return (
        pd.DataFrame(rows)
        .sort_values("Exp pts/game", ascending=False)
        .reset_index(drop=True)
    )


# ── Lineup validator ───────────────────────────────────────────────────────────

def validate_squad(players: pd.DataFrame) -> list[str]:
    """
    Return a list of rule violations for the current squad.
    Empty list = valid squad.
    """
    squad = get_squad(players)
    errors = []

    if squad.empty:
        return ["No players marked as in_squad."]

    n = len(squad)
    if n != SQUAD_SIZE:
        errors.append(f"Squad has {n} players — must be exactly {SQUAD_SIZE}.")

    pos_counts = squad["position"].str.upper().value_counts().to_dict()
    gk  = pos_counts.get("GK",  0)
    def_ = pos_counts.get("DEF", 0)
    mid = pos_counts.get("MID", 0)
    fwd = pos_counts.get("FWD", 0)

    if gk != 1:
        errors.append(f"Need exactly 1 GK (have {gk}).")
    if not (3 <= def_ <= 5):
        errors.append(f"Need 3–5 DEF (have {def_}).")
    if not (3 <= mid <= 5):
        errors.append(f"Need 3–5 MID (have {mid}).")
    if not (1 <= fwd <= 3):
        errors.append(f"Need 1–3 FWD (have {fwd}).")

    budget_used = squad_budget_used(squad)
    if budget_used > BUDGET:
        errors.append(
            f"Budget exceeded: {budget_used:,.0f} € used of {BUDGET:,.0f} € max."
        )

    caps = squad[squad["is_captain"].astype(str).str.lower().isin(["true", "1", "yes"])]
    if len(caps) == 0:
        errors.append("No captain selected.")
    elif len(caps) > 1:
        errors.append(f"{len(caps)} players marked as captain — only 1 allowed.")

    return errors
