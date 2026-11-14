# Historical replay (simulated)

`aperture.replay` runs any strategy bar by bar over recorded M1 bars and reports what would
have happened under conservative fill assumptions. Every output row and the summary carry the
label `simulated`. A replay is an evaluation tool, never an acceptance gate (decisions.md,
2026-11-14), and it proves nothing about real fill quality.

Fetching history from Webull is out of scope here; the tool reads files.

## Run

```
python -m aperture.replay \
  --bars data/history/ABC-2026-11.jsonl data/history/XYZ-2026-11.jsonl \
  --strategy aperture.strategy.price_action_v1 \
  --values strategies/price-action-v1/strategy.toml \
  --settings replay.toml \
  --last-entry 15:45 --flatten 15:55 \
  --out out/replay-2026-11
```

- `--strategy` is any importable module exposing `create(values) -> Strategy`; `--values` is
  the TOML passed to it. Comparing two strategy versions = two runs with different modules or
  values over the same bars.
- `--last-entry` and `--flatten` are exchange-time `HH:MM` (normally the `[session]` values of
  `config/bot.toml`). Half days need their own run with the earlier times.
- Exit code 2 and `replay refused: <cause>` on any bad input; nothing is guessed.

## Input: bars JSONL

One completed M1 bar per line, exactly these keys:

```
{"symbol": "ABC", "start": "2026-11-10T15:00:00+00:00", "open": "10.00", "high": "10.05", "low": "9.95", "close": "10.01", "volume": "1200"}
```

`start` is the bar's opening minute in UTC with an explicit offset (`+00:00` or `Z`). Prices
and volume are strings (floats are refused). Within a symbol, starts must strictly increase
across the files in the order given. Bars are grouped into sessions by their exchange-time
date; earlier sessions of the same symbol are passed as `prior_sessions`.

## Settings

All simulation numbers live in a settings TOML with a comment per key. Missing or unknown
keys are refused. Example (tested to load):

```toml
[replay]
# Adverse slippage per simulated fill, percent of the fill price; read by replay/engine.py.
slippage_percent = 0.05
# Minimum adverse slippage per simulated fill, USD per share; read by replay/engine.py.
slippage_min_usd = 0.01
# Bars after an ENTER decision during which the simulated entry may fill; read by replay/engine.py.
entry_wait_bars = 1
# Exchange time zone for session dates and clock parameters (IANA name); read by replay/engine.py.
session_timezone = "America/New_York"
# Regular session open, HH:MM exchange time; no entry is decided on an earlier bar; read by replay/engine.py.
regular_open = "09:30"
# Decimal places for prices, R and percent in CSV and summary outputs; read by replay/report.py.
output_decimal_places = 4
```

Slippage per fill = max(price x `slippage_percent` / 100, `slippage_min_usd`), always against
us.

## Simulation rules

For bar `i` of a session, in this order:

1. **Open position.** A strategy EXIT decided on bar `i-1` closes at bar `i`'s open. A bar at
   or after `--flatten` closes at its open. Otherwise a bar whose low reaches the stop closes
   at the stop, or at the open if the bar opened below the stop (gap).
2. **Pending entry.** A bar at or after `--last-entry` cancels it (missed). A bar whose range
   touches the price window `[entry_low, entry_high]` fills it at **`entry_high` plus
   slippage**, never better. If that bar also reaches the stop, the stop is taken in the same
   bar. After `entry_wait_bars` bars without a touch the entry is missed; it is never chased.
3. **Strategy.** The strategy is evaluated with bars `0..i` only (no look-ahead; tested by
   altering later bars). `quote` is always `None` because bars carry no bid/ask; a strategy
   that needs a quote must refuse on its own. With a position open it receives a
   `PositionView` with quantity 1.

Every exit pays slippage. A position still open on the last bar closes at that bar's close
(`end_of_data`); positions never cross sessions. Each symbol is replayed independently with a
fresh strategy instance, so the one-open-position platform limit is not applied across symbols.
ENTER decisions while a position or an entry is pending are recorded as `ignored`; ENTER
before `regular_open` or without `entry_high`/`stop`, or with the stop not below `entry_high`,
is `rejected` with the cause.

R per trade = (exit - entry) / (entry - stop), where entry already includes slippage.
Percent per trade = (exit - entry) / entry x 100.

## Outputs (in `--out`)

- `trades.csv`: one row per simulated trade: label, strategy, version, symbol, session date,
  setup, signal bar, entry time and price, stop, exit time, price and reason
  (`stop`, `strategy_exit`, `flatten`, `end_of_data`), bars held, R, percent.
- `signals.csv`: every ENTER decision with its outcome (`filled`, `missed`, `rejected`,
  `ignored`) and cause: the input for "what if we had entered".
- `summary.json` (also printed): sessions, bars, signals by outcome, trades, wins, losses,
  win rate, total and average R, max drawdown in R, total and average percent, exits by
  reason, verdict counts.

## Not yet

- Replaying captured L1 quotes (`data/capture/...`) for quote-based entries and spreads.
- A shared portfolio across symbols (one open position at a time, daily loss ceiling).
- Reading the trading calendar for half days automatically.
