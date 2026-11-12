# NBA 3PT Edge Scanner

Read-only prototype for NBA player 3-point made probabilities and public Kalshi market research.

## Deploy
1. Visit https://share.streamlit.io/ and sign in with GitHub.
2. Select repository `milkmanandthefluidgirls/Scanner-Milk`, branch `main`, entry point `app.py`.
3. Deploy. The platform installs dependencies from `requirements.txt`.
4. Open the provided Streamlit URL on your phone.

## How to use
- View today's NBA games and season player forecasts.
- If NBA.com blocks the hosted IP, upload player game logs CSV containing `PLAYER_ID,PLAYER_NAME,TEAM_ABBREVIATION,GAME_DATE,FG3M,FG3A`.
- Fetch public Kalshi market catalog and download it.
- For prices, upload manually verified `player,threshold,ticker` CSV. No fuzzy matching; confirm that YES means the player makes at least the threshold number of threes.
- Download ranked YES/NO research signals.

## Limitations
**Not live trading software.** Player probabilities are an uncalibrated Poisson baseline, with no injury, lineup, minutes or matchup adjustment. Fee estimates are approximations, not actual Kalshi fees. Market catalog discovery and manually mapped orderbooks are implemented, but automated contract matching, combo RFQs, backtesting, persistent scanning and notifications are not. NBA.com may block cloud data requests. No API keys or trading credentials required. Never commit credentials.

## Files
- `app.py` — Streamlit mobile-friendly dashboard
- `model.py` — baseline probabilities and EV screening
- `sources.py` — NBA stats and Kalshi public APIs
- `contract_mapping_example.csv` — template for verified ticker mapping
- `requirements.txt` — dependencies
