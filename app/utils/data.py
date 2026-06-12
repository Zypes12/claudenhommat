import pandas as pd
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent.parent / "Data"

SCHEMAS = {
    "fixtures.csv": [
        "match_id", "matchday", "stage", "date", "time_uk",
        "home_team", "away_team", "group",
    ],
    "players.csv": [
        "name", "value", "position", "penalties",
        "penalty_taker", "set_piece_role", "in_squad", "is_captain",
    ],
    "groups.csv": [
        "team", "group", "fifa_ranking",
    ],
    "form.csv": [
        "region", "pos", "team", "p", "w", "d", "l",
        "f", "a", "gd", "pts", "last_10",
    ],
    "lineups.csv": ["team", "player_name", "position", "formation"],
    "results.csv": [
        "match_id", "date", "home_team", "away_team",
        "home_score", "away_score", "goalscorers", "assists",
    ],
    "player_stats.csv": [
        "match_id", "date", "player_name", "team", "opponent",
        "goals", "assists", "started", "minutes", "position",
    ],
}

# Columns that should stay numeric; everything else is cast to string on load.
NUMERIC_COLUMNS = {
    "fixtures.csv":  ["match_id", "matchday"],
    "groups.csv":    ["fifa_ranking"],
    "form.csv":      ["pos", "p", "w", "d", "l", "f", "a", "gd", "pts"],
    "results.csv":      ["match_id", "home_score", "away_score"],
    "player_stats.csv": ["goals", "assists", "started", "minutes"],
}


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    return df


def _coerce_types(df: pd.DataFrame, filename: str) -> pd.DataFrame:
    """
    Read everything as string first to avoid float-NaN on empty columns,
    then convert known numeric columns back to numeric.
    """
    numeric_cols = NUMERIC_COLUMNS.get(filename, [])
    for col in df.columns:
        if col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        else:
            df[col] = df[col].fillna("").astype(str).replace("nan", "")
    return df


def load_csv(filename: str) -> pd.DataFrame:
    path = DATA_DIR / filename
    if path.exists():
        try:
            df = pd.read_csv(path, dtype=str)
            df = _normalize_columns(df)
            df = _coerce_types(df, filename)
            if not df.empty:
                return df
        except Exception:
            pass
    return pd.DataFrame(columns=SCHEMAS.get(filename, []))


def save_csv(filename: str, df: pd.DataFrame) -> None:
    path = DATA_DIR / filename
    df.to_csv(path, index=False)
