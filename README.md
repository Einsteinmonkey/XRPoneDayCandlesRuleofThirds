# XRP/USDT Rule of Thirds Calculator

This repo automatically calculates the **Rule of Thirds** levels for **XRP/USDT** using **1-day candles**.

It uses the latest fully closed daily candle, then calculates:

```text
range = high - low
one_third = range / 3
level_1 = low + one_third
level_2 = level_1 + one_third
level_3 = level_2 + one_third
```

## What it produces

Every run updates:

- `results/latest.md` — latest answer in a clean table
- `results/history.csv` — historical daily results
- GitHub Actions summary — quick answer inside the workflow run

## Data source

The script uses Binance public market data:

```text
GET https://api.binance.com/api/v3/klines?symbol=XRPUSDT&interval=1d&limit=5
```

No API key is needed because this is public candle data only.

## Automatic schedule

The GitHub Actions workflow runs every day at **00:17 UTC**, shortly after the Binance daily candle closes at 00:00 UTC.

You can also run it manually from:

```text
GitHub repo → Actions → Daily XRP Rule of Thirds → Run workflow
```

## How to create the repo

1. Create a new GitHub repository, for example:

```text
xrp-rule-of-thirds
```

2. Upload these files and folders:

```text
calculate_rule_of_thirds.py
README.md
.gitignore
.github/workflows/daily-rule-of-thirds.yml
results/.gitkeep
```

3. Go to:

```text
Repo → Settings → Actions → General → Workflow permissions
```

4. Select:

```text
Read and write permissions
```

5. Go to:

```text
Repo → Actions → Daily XRP Rule of Thirds → Run workflow
```

After it runs, open:

```text
results/latest.md
```

## Run locally

```bash
python calculate_rule_of_thirds.py
```

Print JSON:

```bash
python calculate_rule_of_thirds.py --json
```

Use another symbol:

```bash
python calculate_rule_of_thirds.py --symbol BTCUSDT --interval 1d
```

## Important note

This is an educational calculator only. It does not place trades and it is not financial advice.
