# Project: Futispörssi World Cup 2026 Assistant

## Scope and boundaries
- This project lives entirely inside this folder (`FP/`). Do not read, write, or
  reference any files outside this project directory.
- All input data files belong in the `data/` folder.
- All app code belongs in the `app/` folder.

## What this project is
A local app to help me pick the best Futispörssi team and substitution strategy
for World Cup 2026. The app should recommend:
- Which players to transfer in/out each matchday
- Who to captain
- The optimal starting lineup and bench

## Tech stack
- Python 3 + Streamlit for the UI
- Pandas for data handling
- Data stored as local CSV/Excel files in `data/` (no database needed initially)
- Structure code so it could later be redeployed as a web app with minimal changes
  (keep UI logic separate from data/recommendation logic)

## App structure (two pages)

### Page 1: "Data Input"
User-editable tables/forms for:
- Upcoming fixtures (teams, dates, FIFA rankings or difficulty rating)
- Match results already played (scores)
- Goalscorers per match
- Player prices (updated daily — should be easy to bulk-edit)
- Lineups/bench status after each matchday (per player: started / benched / not in squad)
- Player metadata: team, position, price, set-piece roles (penalty taker,
  free-kick taker), current form rating
- Team form/rating per nation

### Page 2: "Recommendations"
Based on Page 1 data, show:
- Recommended transfers in/out for the upcoming matchday, with reasoning
- Recommended starting XI and bench
- Recommended captain (and reasoning)
- Summary of upcoming fixture difficulty for my current squad

## Recommendation logic — factors to weigh
When suggesting transfers, captaincy, and lineup, consider:
1. Fixture difficulty (based on opponent FIFA ranking)
2. Set-piece duty (penalty takers and free-kick takers score more)
3. Path to knockout stage (teams likely to play more games are safer long-term picks)
4. Player current form
5. Team current form
6. Player price (value for money, budget constraints)
7. Lineup status (don't recommend players who were benched/dropped recently)
8. Upcoming fixture run (not just next match, but next 2-3)

Weight these sensibly — fixture difficulty, form, and lineup status (starting XI
vs benched) should matter most. Explain *why* a recommendation is made in plain
language, not just a score.

## Data files I will provide/maintain in `data/`
- `fixtures.csv` — match schedule, FIFA rankings, dates
- `players.csv` — player name, team, position, price, penalty/free-kick flags
- `results.csv` — finished match scores and goalscorers
- `lineups.csv` — per-matchday lineup status per player
- `my_squad.csv` — my current team, budget, captain

(Claude: if these files don't exist yet, create empty templates with sensible
column headers based on the descriptions above, and ask me to fill them in or
help me fill them in.)

## My background
I have no coding background. Please:
- Explain setup steps clearly and in order
- Avoid jargon, or explain it briefly when used
- Tell me exactly what command to run and where
- Point out if something needs to be installed (e.g. Python, pip packages)

## Running the app
The app should be runnable with a single command, e.g.:
```
streamlit run app/main.py
```
Document any setup/install steps needed in `README.md`.
