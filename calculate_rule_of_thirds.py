#!/usr/bin/env python3
"""XRP/USDT daily candle Rule of Thirds calculator.

Uses OKX public market candles so it can run from GitHub Actions without a key.
Generates a GitHub Pages homepage with the latest result plus the most recent
10 fully closed daily candles.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, getcontext
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

getcontext().prec = 28

OKX_BASE_URL = "https://www.okx.com"
OKX_CANDLES_ENDPOINT = "/api/v5/market/candles"
DEFAULT_SYMBOL = "XRP-USDT"
# 1Dutc keeps the daily candle aligned to 00:00 UTC.
DEFAULT_INTERVAL = "1Dutc"
DEFAULT_DAYS = 10


@dataclass(frozen=True)
class RuleOfThirdsResult:
    symbol: str
    interval: str
    candle_date_utc: str
    candle_open_time_utc: str
    candle_close_time_utc: str
    high: str
    low: str
    range: str
    one_third: str
    level_1_low_third: str
    level_2_middle: str
    level_3_high_average: str
    calculated_at_utc: str
    data_source: str


def normalize_symbol(symbol: str) -> str:
    """Convert XRPUSDT or XRP/USDT to OKX format XRP-USDT."""
    s = symbol.strip().upper().replace("/", "-").replace("_", "-")
    if "-" in s:
        return s
    known_quotes = ("USDT", "USD", "USDC", "BTC", "ETH")
    for quote in known_quotes:
        if s.endswith(quote) and len(s) > len(quote):
            return f"{s[:-len(quote)]}-{quote}"
    return s


def normalize_interval(interval: str) -> str:
    """Map Binance-style 1d to OKX UTC daily candle format."""
    i = interval.strip()
    mapping = {
        "1d": "1Dutc",
        "1D": "1Dutc",
        "1day": "1Dutc",
        "1Day": "1Dutc",
        "1Dutc": "1Dutc",
        "1DUTC": "1Dutc",
    }
    return mapping.get(i, i)


def utc_from_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def interval_to_ms(interval: str) -> int | None:
    """Return milliseconds for fixed-length OKX intervals."""
    interval = interval.replace("utc", "").replace("UTC", "")
    match = re.fullmatch(r"(\d+)([smHDWM])", interval)
    if not match:
        return None

    amount = int(match.group(1))
    unit = match.group(2)
    multipliers: dict[str, int | None] = {
        "s": 1000,
        "m": 60 * 1000,
        "H": 60 * 60 * 1000,
        "D": 24 * 60 * 60 * 1000,
        "W": 7 * 24 * 60 * 60 * 1000,
        "M": None,  # month length varies
    }
    multiplier = multipliers[unit]
    if multiplier is None:
        return None
    return amount * multiplier


def decimal_to_string(value: Decimal, places: int = 8) -> str:
    quant = Decimal("1").scaleb(-places)
    rounded = value.quantize(quant)
    return format(rounded.normalize(), "f")


def fetch_okx_candles(symbol: str, interval: str, limit: int = 30) -> list[list[Any]]:
    inst_id = normalize_symbol(symbol)
    bar = normalize_interval(interval)
    params = urlencode({"instId": inst_id, "bar": bar, "limit": str(limit)})
    url = f"{OKX_BASE_URL}{OKX_CANDLES_ENDPOINT}?{params}"
    request = Request(url, headers={"User-Agent": "xrp-rule-of-thirds/2.0"})

    try:
        with urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OKX API HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not connect to OKX API: {exc.reason}") from exc

    data = json.loads(body)
    if not isinstance(data, dict) or data.get("code") != "0":
        raise RuntimeError(f"Unexpected OKX API response: {data}")

    candles = data.get("data", [])
    if not isinstance(candles, list) or not candles:
        raise RuntimeError(f"No candle data returned for {inst_id} {bar}.")
    return candles


def select_closed_okx_candles(candles: list[list[Any]], interval: str, days: int) -> list[list[Any]]:
    """OKX candle format: [ts, open, high, low, close, vol, volCcy, volCcyQuote, confirm]."""
    now_ms = int(time.time() * 1000)
    bar_ms = interval_to_ms(normalize_interval(interval))

    closed: list[list[Any]] = []
    for candle in candles:
        if len(candle) < 5:
            continue
        open_time_ms = int(candle[0])
        confirm = str(candle[8]) if len(candle) > 8 else ""
        confirmed_by_api = confirm == "1"
        confirmed_by_time = bar_ms is not None and open_time_ms + bar_ms <= now_ms
        if confirmed_by_api or confirmed_by_time:
            closed.append(candle)

    if not closed:
        raise RuntimeError("No fully closed candle found in the returned OKX candle data.")

    # OKX commonly returns newest first; sorting keeps the page chronological.
    closed_sorted = sorted(closed, key=lambda row: int(row[0]))
    return closed_sorted[-days:]


def result_from_candle(symbol: str, interval: str, candle: list[Any], calculated_at: datetime) -> RuleOfThirdsResult:
    open_time_ms = int(candle[0])
    high = Decimal(str(candle[2]))
    low = Decimal(str(candle[3]))

    close_time_ms = open_time_ms
    bar_ms = interval_to_ms(interval)
    if bar_ms is not None:
        close_time_ms = open_time_ms + bar_ms - 1

    candle_range = high - low
    one_third = candle_range / Decimal("3")
    level_1 = low + one_third
    level_2 = level_1 + one_third
    level_3 = level_2 + one_third

    open_dt = utc_from_ms(open_time_ms)
    close_dt = utc_from_ms(close_time_ms)

    return RuleOfThirdsResult(
        symbol=symbol,
        interval=interval,
        candle_date_utc=open_dt.date().isoformat(),
        candle_open_time_utc=open_dt.isoformat(),
        candle_close_time_utc=close_dt.isoformat(),
        high=decimal_to_string(high),
        low=decimal_to_string(low),
        range=decimal_to_string(candle_range),
        one_third=decimal_to_string(one_third),
        level_1_low_third=decimal_to_string(level_1),
        level_2_middle=decimal_to_string(level_2),
        level_3_high_average=decimal_to_string(level_3),
        calculated_at_utc=calculated_at.isoformat(),
        data_source="OKX public candles",
    )


def calculate(symbol: str = DEFAULT_SYMBOL, interval: str = DEFAULT_INTERVAL) -> RuleOfThirdsResult:
    return calculate_last_n_days(symbol=symbol, interval=interval, days=1)[-1]


def calculate_last_n_days(symbol: str = DEFAULT_SYMBOL, interval: str = DEFAULT_INTERVAL, days: int = DEFAULT_DAYS) -> list[RuleOfThirdsResult]:
    okx_symbol = normalize_symbol(symbol)
    okx_interval = normalize_interval(interval)
    candles = fetch_okx_candles(symbol=okx_symbol, interval=okx_interval, limit=max(days + 5, 20))
    closed_candles = select_closed_okx_candles(candles, okx_interval, days=days)
    calculated_at = datetime.now(timezone.utc)
    return [result_from_candle(okx_symbol, okx_interval, candle, calculated_at) for candle in closed_candles]


def write_latest_markdown(result: RuleOfThirdsResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = f"""# XRP/USDT Rule of Thirds Result

| Field | Value |
|---|---:|
| Symbol | {result.symbol} |
| Interval | {result.interval} |
| Candle date UTC | {result.candle_date_utc} |
| High | {result.high} |
| Low | {result.low} |
| Range | {result.range} |
| One Third | {result.one_third} |
| Level 1 | {result.level_1_low_third} |
| Level 2 / Middle | {result.level_2_middle} |
| Level 3 / High Average | {result.level_3_high_average} |
| Data Source | {result.data_source} |
| Calculated at UTC | {result.calculated_at_utc} |
"""
    path.write_text(content, encoding="utf-8")


def write_last_10_markdown(results: list[RuleOfThirdsResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        "# XRP/USDT Rule of Thirds - Last 10 Closed Daily Candles\n",
        "| Date UTC | Low | High | Range | 1/3 | Level 1 | Level 2 / Middle | Level 3 / High Avg |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for result in reversed(results):
        rows.append(
            f"| {result.candle_date_utc} | {result.low} | {result.high} | {result.range} | "
            f"{result.one_third} | {result.level_1_low_third} | {result.level_2_middle} | {result.level_3_high_average} |"
        )
    rows.append("")
    path.write_text("\n".join(rows), encoding="utf-8")


def write_index_html(results: list[RuleOfThirdsResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    if not results:
        raise RuntimeError("No results available to write index.html")

    latest = results[-1]

    def e(value: str) -> str:
        return html.escape(str(value), quote=True)

    table_rows = "\n".join(
        f"""      <tr>
        <td>{e(result.candle_date_utc)}</td>
        <td>{e(result.low)}</td>
        <td>{e(result.high)}</td>
        <td>{e(result.one_third)}</td>
        <td>{e(result.level_1_low_third)}</td>
        <td>{e(result.level_2_middle)}</td>
        <td>{e(result.level_3_high_average)}</td>
      </tr>"""
        for result in reversed(results)
    )

    content = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>XRP/USDT Rule of Thirds</title>
  <style>
    :root {{
      color-scheme: dark;
      font-family: Arial, Helvetica, sans-serif;
      background: #0d1117;
      color: #f0f6fc;
    }}
    body {{
      margin: 0;
      min-height: 100vh;
      display: flex;
      align-items: flex-start;
      justify-content: center;
      padding: 32px 16px;
      box-sizing: border-box;
      background:
        radial-gradient(circle at top left, rgba(88,166,255,.16), transparent 30%),
        #0d1117;
    }}
    main {{
      width: min(1040px, 100%);
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 20px;
      padding: 28px;
      box-shadow: 0 20px 60px rgba(0,0,0,.35);
    }}
    h1 {{
      margin: 0 0 6px;
      font-size: clamp(30px, 5vw, 48px);
      letter-spacing: -.03em;
    }}
    h2 {{
      margin: 28px 0 12px;
      font-size: 20px;
    }}
    .subtitle {{
      margin: 0 0 24px;
      color: #8b949e;
      font-size: 15px;
    }}
    .price-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
      margin-bottom: 14px;
    }}
    .card {{
      border: 1px solid #30363d;
      background: #0d1117;
      border-radius: 16px;
      padding: 18px;
    }}
    .label {{
      display: block;
      color: #8b949e;
      font-size: 13px;
      margin-bottom: 8px;
      text-transform: uppercase;
      letter-spacing: .06em;
    }}
    .value {{
      font-size: clamp(24px, 6vw, 40px);
      font-weight: 800;
      line-height: 1.05;
      word-break: break-word;
    }}
    .latest-table {{
      margin-top: 14px;
    }}
    .table-wrap {{
      overflow-x: auto;
      border: 1px solid #30363d;
      border-radius: 16px;
      background: #0d1117;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 820px;
    }}
    th, td {{
      padding: 14px 12px;
      border-bottom: 1px solid #30363d;
      text-align: left;
      white-space: nowrap;
    }}
    th {{ color: #8b949e; font-weight: 700; }}
    td:not(:first-child), th:not(:first-child) {{ text-align: right; font-weight: 700; }}
    tr:last-child td {{ border-bottom: 0; }}
    .meta {{
      margin-top: 18px;
      color: #8b949e;
      font-size: 13px;
      line-height: 1.5;
    }}
    .note {{
      color: #8b949e;
      font-size: 13px;
      margin-top: 10px;
    }}
    @media (max-width: 620px) {{
      main {{ padding: 20px; }}
      .price-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <main>
    <h1>{e(latest.symbol)} Rule of Thirds</h1>
    <p class="subtitle">Latest closed 1-day candle · {e(latest.candle_date_utc)}</p>

    <section class="price-grid">
      <div class="card">
        <span class="label">Latest Low</span>
        <div class="value">{e(latest.low)}</div>
      </div>
      <div class="card">
        <span class="label">Latest High</span>
        <div class="value">{e(latest.high)}</div>
      </div>
    </section>

    <div class="table-wrap latest-table">
      <table aria-label="Latest XRP USDT rule of thirds result">
        <tr><th>Latest Result</th><th>Price</th></tr>
        <tr><td>Range</td><td>{e(latest.range)}</td></tr>
        <tr><td>One Third</td><td>{e(latest.one_third)}</td></tr>
        <tr><td>Level 1</td><td>{e(latest.level_1_low_third)}</td></tr>
        <tr><td>Level 2 / Middle</td><td>{e(latest.level_2_middle)}</td></tr>
        <tr><td>Level 3 / High Average</td><td>{e(latest.level_3_high_average)}</td></tr>
      </table>
    </div>

    <h2>Most Recent 10 Closed Daily Candles</h2>
    <div class="table-wrap">
      <table aria-label="Last 10 XRP USDT rule of thirds results">
        <tr>
          <th>Date UTC</th>
          <th>Low</th>
          <th>High</th>
          <th>1/3</th>
          <th>Level 1</th>
          <th>Level 2 / Middle</th>
          <th>Level 3 / High Avg</th>
        </tr>
{table_rows}
      </table>
    </div>

    <p class="meta">
      Latest candle close UTC: {e(latest.candle_close_time_utc)}<br>
      Last updated UTC: {e(latest.calculated_at_utc)}<br>
      Source: {e(latest.data_source)}
    </p>
    <p class="note">The page updates when the GitHub Action runs after the UTC daily candle closes.</p>
  </main>
</body>
</html>
"""
    path.write_text(content, encoding="utf-8")


def append_history_csv(results: list[RuleOfThirdsResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(asdict(results[0]).keys())

    existing: dict[tuple[str, str, str], dict[str, str]] = {}
    if path.exists():
        with path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                existing[(row.get("symbol", ""), row.get("interval", ""), row.get("candle_date_utc", ""))] = row

    for result in results:
        existing[(result.symbol, result.interval, result.candle_date_utc)] = {k: str(v) for k, v in asdict(result).items()}

    ordered_rows = sorted(existing.values(), key=lambda row: row.get("candle_date_utc", ""))
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(ordered_rows)


def write_github_summary(results: list[RuleOfThirdsResult]) -> None:
    import os

    env_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not env_path or not results:
        return

    latest = results[-1]
    with Path(env_path).open("a", encoding="utf-8") as f:
        f.write("## XRP/USDT Rule of Thirds\n\n")
        f.write(f"Latest closed candle UTC: **{latest.candle_date_utc}**\n\n")
        f.write(f"- Low: **{latest.low}**\n")
        f.write(f"- High: **{latest.high}**\n")
        f.write(f"- One third: **{latest.one_third}**\n")
        f.write(f"- Level 1: **{latest.level_1_low_third}**\n")
        f.write(f"- Level 2 / Middle: **{latest.level_2_middle}**\n")
        f.write(f"- Level 3 / High Average: **{latest.level_3_high_average}**\n")
        f.write(f"- Source: **{latest.data_source}**\n\n")
        f.write("### Last 10 closed daily candles\n\n")
        f.write("| Date | Low | High | Level 1 | Middle | High Avg |\n")
        f.write("|---|---:|---:|---:|---:|---:|\n")
        for result in reversed(results):
            f.write(
                f"| {result.candle_date_utc} | {result.low} | {result.high} | "
                f"{result.level_1_low_third} | {result.level_2_middle} | {result.level_3_high_average} |\n"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Calculate daily XRP/USDT Rule of Thirds levels.")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL, help="Trading pair symbol, default: XRP-USDT")
    parser.add_argument("--interval", default=DEFAULT_INTERVAL, help="Candle interval, default: 1Dutc")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="Number of closed daily candles to show on the page, default: 10")
    parser.add_argument("--latest-md", default="results/latest.md", help="Path for latest markdown output")
    parser.add_argument("--last-10-md", default="results/last_10.md", help="Path for last 10 markdown output")
    parser.add_argument("--history-csv", default="results/history.csv", help="Path for history CSV output")
    parser.add_argument("--index-html", default="index.html", help="Path for public GitHub Pages homepage")
    parser.add_argument("--json", action="store_true", help="Print JSON only")
    args = parser.parse_args()

    if args.days < 1:
        raise RuntimeError("--days must be at least 1")

    results = calculate_last_n_days(symbol=args.symbol, interval=args.interval, days=args.days)
    latest = results[-1]

    write_latest_markdown(latest, Path(args.latest_md))
    write_last_10_markdown(results, Path(args.last_10_md))
    write_index_html(results, Path(args.index_html))
    append_history_csv(results, Path(args.history_csv))
    write_github_summary(results)

    if args.json:
        print(json.dumps([asdict(result) for result in results], indent=2))
    else:
        print(f"{latest.symbol} {latest.interval} latest closed candle UTC: {latest.candle_date_utc}")
        print(f"Low: {latest.low}")
        print(f"High: {latest.high}")
        print(f"Range: {latest.range}")
        print(f"One third: {latest.one_third}")
        print(f"Level 1 / Low third: {latest.level_1_low_third}")
        print(f"Level 2 / Middle: {latest.level_2_middle}")
        print(f"Level 3 / High average: {latest.level_3_high_average}")
        print(f"Wrote {len(results)} closed daily candles to index.html")
        print(f"Source: {latest.data_source}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
