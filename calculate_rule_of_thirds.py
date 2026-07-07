#!/usr/bin/env python3
"""XRP/USDT daily candle Rule of Thirds calculator.

Uses OKX public market candles instead of Binance because Binance can return
HTTP 451 from GitHub-hosted runners in restricted locations.
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
    """Convert XRPUSDT to OKX format XRP-USDT, while allowing XRP-USDT."""
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
    # Handles the intervals this project needs and common OKX intervals.
    interval = interval.replace("utc", "").replace("UTC", "")
    match = re.fullmatch(r"(\d+)([smHDWM])", interval)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2)
    return {
        "s": 1000,
        "m": 60 * 1000,
        "H": 60 * 60 * 1000,
        "D": 24 * 60 * 60 * 1000,
        "W": 7 * 24 * 60 * 60 * 1000,
        # Month length varies, so do not calculate a fixed close time for M.
        "M": None,
    }[unit] and amount * {
        "s": 1000,
        "m": 60 * 1000,
        "H": 60 * 60 * 1000,
        "D": 24 * 60 * 60 * 1000,
        "W": 7 * 24 * 60 * 60 * 1000,
        "M": 0,
    }[unit]


def decimal_to_string(value: Decimal, places: int = 8) -> str:
    quant = Decimal("1").scaleb(-places)
    rounded = value.quantize(quant)
    return format(rounded.normalize(), "f")


def fetch_okx_candles(symbol: str, interval: str, limit: int = 5) -> list[list[Any]]:
    inst_id = normalize_symbol(symbol)
    bar = normalize_interval(interval)
    params = urlencode({"instId": inst_id, "bar": bar, "limit": str(limit)})
    url = f"{OKX_BASE_URL}{OKX_CANDLES_ENDPOINT}?{params}"
    request = Request(url, headers={"User-Agent": "xrp-rule-of-thirds/1.1"})

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


def select_latest_closed_okx_candle(candles: list[list[Any]], interval: str) -> list[Any]:
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
        confirmed_by_time = (bar_ms is not None and open_time_ms + bar_ms <= now_ms)
        if confirmed_by_api or confirmed_by_time:
            closed.append(candle)

    if not closed:
        raise RuntimeError("No fully closed candle found in the returned OKX candle data.")

    # OKX commonly returns newest first; sorting avoids relying on response order.
    return sorted(closed, key=lambda row: int(row[0]))[-1]


def calculate(symbol: str = DEFAULT_SYMBOL, interval: str = DEFAULT_INTERVAL) -> RuleOfThirdsResult:
    okx_symbol = normalize_symbol(symbol)
    okx_interval = normalize_interval(interval)
    candles = fetch_okx_candles(symbol=okx_symbol, interval=okx_interval, limit=5)
    candle = select_latest_closed_okx_candle(candles, okx_interval)

    open_time_ms = int(candle[0])
    high = Decimal(str(candle[2]))
    low = Decimal(str(candle[3]))

    close_time_ms = open_time_ms
    bar_ms = interval_to_ms(okx_interval)
    if bar_ms is not None:
        close_time_ms = open_time_ms + bar_ms - 1

    candle_range = high - low
    one_third = candle_range / Decimal("3")
    level_1 = low + one_third
    level_2 = level_1 + one_third
    level_3 = level_2 + one_third

    open_dt = utc_from_ms(open_time_ms)
    close_dt = utc_from_ms(close_time_ms)
    calculated_at = datetime.now(timezone.utc)

    return RuleOfThirdsResult(
        symbol=okx_symbol,
        interval=okx_interval,
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


def write_index_html(result: RuleOfThirdsResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    def e(value: str) -> str:
        return html.escape(str(value), quote=True)

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
      align-items: center;
      justify-content: center;
      padding: 32px 16px;
      box-sizing: border-box;
    }}
    main {{
      width: min(760px, 100%);
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 20px;
      padding: 28px;
      box-shadow: 0 20px 60px rgba(0,0,0,.35);
    }}
    h1 {{
      margin: 0 0 6px;
      font-size: clamp(28px, 5vw, 44px);
      letter-spacing: -.03em;
    }}
    .subtitle {{
      margin: 0 0 26px;
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
    table {{
      width: 100%;
      border-collapse: collapse;
      overflow: hidden;
      border-radius: 14px;
      margin-top: 14px;
    }}
    th, td {{
      padding: 14px 12px;
      border-bottom: 1px solid #30363d;
      text-align: left;
    }}
    th {{ color: #8b949e; font-weight: 600; }}
    td:last-child, th:last-child {{ text-align: right; font-weight: 700; }}
    tr:last-child td {{ border-bottom: 0; }}
    .meta {{
      margin-top: 18px;
      color: #8b949e;
      font-size: 13px;
      line-height: 1.5;
    }}
    @media (max-width: 620px) {{
      main {{ padding: 20px; }}
      .price-grid {{ grid-template-columns: 1fr; }}
      th, td {{ padding: 12px 8px; }}
    }}
  </style>
</head>
<body>
  <main>
    <h1>{e(result.symbol)} Rule of Thirds</h1>
    <p class="subtitle">1-day candle · {e(result.candle_date_utc)}</p>

    <section class="price-grid">
      <div class="card">
        <span class="label">Low</span>
        <div class="value">{e(result.low)}</div>
      </div>
      <div class="card">
        <span class="label">High</span>
        <div class="value">{e(result.high)}</div>
      </div>
    </section>

    <table aria-label="XRP USDT rule of thirds results">
      <tr><th>Result</th><th>Price</th></tr>
      <tr><td>Range</td><td>{e(result.range)}</td></tr>
      <tr><td>One Third</td><td>{e(result.one_third)}</td></tr>
      <tr><td>Level 1</td><td>{e(result.level_1_low_third)}</td></tr>
      <tr><td>Level 2 / Middle</td><td>{e(result.level_2_middle)}</td></tr>
      <tr><td>Level 3 / High Average</td><td>{e(result.level_3_high_average)}</td></tr>
    </table>

    <p class="meta">
      Candle close UTC: {e(result.candle_close_time_utc)}<br>
      Last updated UTC: {e(result.calculated_at_utc)}<br>
      Source: {e(result.data_source)}
    </p>
  </main>
</body>
</html>
"""
    path.write_text(content, encoding="utf-8")


def append_history_csv(result: RuleOfThirdsResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(asdict(result).keys())
    file_exists = path.exists()

    existing_keys: set[tuple[str, str, str]] = set()
    if file_exists:
        with path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                existing_keys.add((row.get("symbol", ""), row.get("interval", ""), row.get("candle_date_utc", "")))

    key = (result.symbol, result.interval, result.candle_date_utc)
    if key in existing_keys:
        return

    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow(asdict(result))


def write_github_summary(result: RuleOfThirdsResult) -> None:
    import os

    env_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not env_path:
        return

    with Path(env_path).open("a", encoding="utf-8") as f:
        f.write("## XRP/USDT Rule of Thirds\n\n")
        f.write(f"- Candle date UTC: **{result.candle_date_utc}**\n")
        f.write(f"- Low: **{result.low}**\n")
        f.write(f"- High: **{result.high}**\n")
        f.write(f"- One third: **{result.one_third}**\n")
        f.write(f"- Level 1: **{result.level_1_low_third}**\n")
        f.write(f"- Level 2 / Middle: **{result.level_2_middle}**\n")
        f.write(f"- Level 3 / High Average: **{result.level_3_high_average}**\n")
        f.write(f"- Source: **{result.data_source}**\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Calculate daily XRP/USDT Rule of Thirds levels.")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL, help="Trading pair symbol, default: XRP-USDT")
    parser.add_argument("--interval", default=DEFAULT_INTERVAL, help="Candle interval, default: 1Dutc")
    parser.add_argument("--latest-md", default="results/latest.md", help="Path for latest markdown output")
    parser.add_argument("--history-csv", default="results/history.csv", help="Path for history CSV output")
    parser.add_argument("--index-html", default="index.html", help="Path for public GitHub Pages homepage")
    parser.add_argument("--json", action="store_true", help="Print JSON only")
    args = parser.parse_args()

    result = calculate(symbol=args.symbol, interval=args.interval)

    write_latest_markdown(result, Path(args.latest_md))
    write_index_html(result, Path(args.index_html))
    append_history_csv(result, Path(args.history_csv))
    write_github_summary(result)

    if args.json:
        print(json.dumps(asdict(result), indent=2))
    else:
        print(f"{result.symbol} {result.interval} candle date UTC: {result.candle_date_utc}")
        print(f"Low: {result.low}")
        print(f"High: {result.high}")
        print(f"Range: {result.range}")
        print(f"One third: {result.one_third}")
        print(f"Level 1 / Low third: {result.level_1_low_third}")
        print(f"Level 2 / Middle: {result.level_2_middle}")
        print(f"Level 3 / High average: {result.level_3_high_average}")
        print(f"Source: {result.data_source}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
