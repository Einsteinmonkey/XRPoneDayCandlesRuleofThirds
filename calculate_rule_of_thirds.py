#!/usr/bin/env python3
"""XRP/USDT daily candle Rule of Thirds calculator."""

from __future__ import annotations

import argparse
import csv
import html
import json
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

BINANCE_BASE_URL = "https://api.binance.com"
KLINES_ENDPOINT = "/api/v3/klines"
DEFAULT_SYMBOL = "XRPUSDT"
DEFAULT_INTERVAL = "1d"


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


def utc_from_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def decimal_to_string(value: Decimal, places: int = 8) -> str:
    quant = Decimal("1").scaleb(-places)
    rounded = value.quantize(quant)
    return format(rounded.normalize(), "f")


def fetch_klines(symbol: str, interval: str, limit: int = 5) -> list[list[Any]]:
    params = urlencode({"symbol": symbol.upper(), "interval": interval, "limit": limit})
    url = f"{BINANCE_BASE_URL}{KLINES_ENDPOINT}?{params}"
    request = Request(url, headers={"User-Agent": "xrp-rule-of-thirds/1.0"})

    try:
        with urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Binance API HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not connect to Binance API: {exc.reason}") from exc

    data = json.loads(body)
    if not isinstance(data, list):
        raise RuntimeError(f"Unexpected Binance API response: {data}")
    return data


def select_latest_closed_kline(klines: list[list[Any]]) -> list[Any]:
    now_ms = int(time.time() * 1000)
    closed = [k for k in klines if int(k[6]) <= now_ms]
    if not closed:
        raise RuntimeError("No fully closed candle found in the returned kline data.")
    return closed[-1]


def calculate(symbol: str = DEFAULT_SYMBOL, interval: str = DEFAULT_INTERVAL) -> RuleOfThirdsResult:
    klines = fetch_klines(symbol=symbol, interval=interval, limit=5)
    kline = select_latest_closed_kline(klines)

    open_time_ms = int(kline[0])
    high = Decimal(str(kline[2]))
    low = Decimal(str(kline[3]))
    close_time_ms = int(kline[6])

    candle_range = high - low
    one_third = candle_range / Decimal("3")
    level_1 = low + one_third
    level_2 = level_1 + one_third
    level_3 = level_2 + one_third

    open_dt = utc_from_ms(open_time_ms)
    close_dt = utc_from_ms(close_time_ms)
    calculated_at = datetime.now(timezone.utc)

    return RuleOfThirdsResult(
        symbol=symbol.upper(),
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
    )


def write_latest_markdown(result: RuleOfThirdsResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = f"""# XRP/USDT Rule of Thirds Result

| Field | Value |
|---|---:|
| Symbol | {result.symbol} |
| Candle date UTC | {result.candle_date_utc} |
| High | {result.high} |
| Low | {result.low} |
| Range | {result.range} |
| One Third | {result.one_third} |
| Level 1 | {result.level_1_low_third} |
| Level 2 / Middle | {result.level_2_middle} |
| Level 3 / High Average | {result.level_3_high_average} |
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
      Last updated UTC: {e(result.calculated_at_utc)}
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Calculate daily XRP/USDT Rule of Thirds levels.")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL, help="Trading pair symbol, default: XRPUSDT")
    parser.add_argument("--interval", default=DEFAULT_INTERVAL, help="Kline interval, default: 1d")
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

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
