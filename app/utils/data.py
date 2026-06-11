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
        "home_score", "away_score", "goalscorers",
    ],
}


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    return df


def load_csv(filename: str) -> pd.DataFrame:
    path = DATA_DIR / filename
    if path.exists():
        try:
            df = pd.read_csv(path)
            df = _normalize_columns(df)
            if not df.empty:
                return df
        except Exception:
            pass
    return pd.DataFrame(columns=SCHEMAS.get(filename, []))


def save_csv(filename: str, df: pd.DataFrame) -> None:
    path = DATA_DIR / filename
    df.to_csv(path, index=False)
