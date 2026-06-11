# Futispörssi World Cup 2026 — Setup & Usage

## One-time setup (do this once)

### Step 1 — Install Xcode Command Line Tools
Open **Terminal** and run:
```
xcode-select --install
```
A dialog will appear — click **Install** and wait (~5 minutes). This installs the tools macOS needs to run Python.

### Step 2 — Install Python
Go to https://www.python.org/downloads/ and download the latest **Python 3** installer for macOS. Run the installer and follow the steps.

To confirm it worked, open Terminal and run:
```
python3 --version
```
You should see something like `Python 3.12.4`.

### Step 3 — Install the app's dependencies
In Terminal, navigate to this project folder:
```
cd ~/Desktop/ClaudeProjects/FP
```
Then install the required packages:
```
pip3 install -r requirements.txt
```

---

## Running the app

Every time you want to use the app, open Terminal and run:
```
cd ~/Desktop/ClaudeProjects/FP
streamlit run app/main.py
```
The app will open automatically in your web browser at http://localhost:8501.

To stop the app, press `Ctrl + C` in Terminal.

---

## How to use the app

### Page 1: Data Input
- **My Squad** — your 15 players. Tick is_captain / is_vice_captain for one player each.
- **Players** — the full player database (name, team, position, price, form). Update prices here daily.
- **Fixtures** — the full match schedule with FIFA rankings.
- **Results** — finished match scores and goalscorers.
- **Lineups** — record whether each player started, was benched, or wasn't in the squad each matchday.

Always press **Save** after editing a table.

### Page 2: Recommendations
- Recommended captain with explanation.
- Transfer suggestions ranked by fixture difficulty, form, and set-piece roles.
- Fixture difficulty table for your current squad.

---

## Data files (in Data/)

| File | What it contains |
|---|---|
| `my_squad.csv` | Your current 15-player team |
| `players.csv` | All available players + prices + stats |
| `fixtures.csv` | Full match schedule + FIFA rankings |
| `results.csv` | Played match scores + goalscorers |
| `lineups.csv` | Per-matchday lineup status per player |
