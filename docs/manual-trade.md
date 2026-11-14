# Manual protected trade (stage 3)

Run on the owner's Mac, in market hours, in Sandbox (`[run] environment` in `config/bot.toml`).
Credentials are read from the environment variables named in the active `[environments.*]` table.
For the Monday 2026-11-16 probe day, follow "Probe day" below from top to bottom.

## One-time setup (fresh clone)

```
gh repo clone valentyn-dymaretskyi/Aperture ~/Development/Aperture && cd ~/Development/Aperture
python3.12 -m venv .venv && source .venv/bin/activate     # Python 3.12 or newer
pip install -e ".[webull,dev]"
mkdir -p data/webull-token-sandbox                         # [run] data_dir / [environments.sandbox] token_dir
```

The Sandbox variables (`WEBULL_SANDBOX_APP_KEY`, `WEBULL_SANDBOX_APP_SECRET`,
`WEBULL_SANDBOX_ACCOUNT_ID`, names from `[environments.sandbox]`) live in
`~/.config/aperture/env`, the same file the launchd jobs load (docs/operations.md, one-time
setup step 2). Never type the values on the command line or put them in the repo.

Every command below runs from the repository root: `--config` defaults to the relative path
`config/bot.toml`. In each new terminal:

```
cd ~/Development/Aperture && source .venv/bin/activate
set -a; . ~/.config/aperture/env; set +a
aperture check-config        # must show "trading gate: open (trading enabled by configuration)"
```

The first command that talks to Webull may wait up to `token_check_duration_seconds` (300 s) for
you to approve the new token in the Webull app. When it waits, it prints
`waiting: approve the API token in the Webull app within 300 s` on stderr first. The token is
then kept in `data/webull-token-sandbox/`. A token Webull reports INVALID or EXPIRED, or one not
approved in time, stops the command with `token initialisation failed: <cause>`.

This manual path sends no notifications: everything it has to tell you is printed to the
terminal (errors and `needs the operator: <fence>: <cause>` lines on stderr). Keep the terminal
visible.

## Commands

```
aperture check-config
aperture status                                 # environment, gate, bot pid, last run
aperture check-quote SYMBOL                     # read-only: is the snapshot quote fresh?
aperture trade SYMBOL --limit 12.34            # quantity and stop default from [probe]
aperture trade SYMBOL --limit 12.34 --stop 12.10
aperture probe-replace-stop --to 12.20          # probe F5, see below
aperture resume                                 # recover, keep protecting; ends when no trade is open
aperture exit                                   # recover, then close every open trade
aperture clear-fence NAME                       # after looking at an operator fence
```

Exit codes: 0 done; 2 config error; 3 refused (`refused: <cause>` on stderr; also "another
aperture process holds the lock"); 4 a trade waits for you (closing order refused or not
filled; for `probe-replace-stop`: see the F5 results).

Stop price: without `--stop`, the planned-loss check uses the worst case (2 % below the
limit), and the stop actually placed is `stop_percent_below_fill` below the real average fill
(owner decision 2026-11-14), rounded down to the cent.

What `trade` does: refuses outside the regular session, outside `[probe] window_start..window_end`
(10:00-15:30 ET) or after the last-entry time; refuses a price above `max_price_usd` (50) or a
planned loss above `max_loss_usd` (5). Then: LIMIT BUY (DAY, core session) -> on every observed
fill a SELL STOP_LOSS for exactly that quantity -> once the stop is seen working, hold
`hold_seconds` (60) -> cancel the stop, confirm it is cancelled -> MARKET SELL exactly the held
quantity -> closed when the broker is flat. An entry that does not fill within
`entry_wait_seconds` (60) is cancelled. At the end it prints `trade <id> <symbol>: <state>
(<close cause>)` and every fence still set.

Watch in the Webull app during the run:

1. Right after the fill: one working SELL stop for the filled quantity at the printed stop price.
2. After the close: no position in the symbol and no working orders left.
3. If you see a position without a working stop for more than a few seconds, close it in the
   app and run `aperture status`; the bot fences entries and will not touch it again.

Crash test: do the `kill -9` only while the stop is visible as working in the app, then run
`aperture resume`. It must find the same trade, keep the stop and close at the hold time or at
the flatten time.

If a closing order is refused (`exit-refused:<trade>`), or is still not filled
`[orders] max_exit_seconds` (30 s) after it was sent and then ends (`exit-not-filled:<trade>`),
the bot puts the stop back, sends no second close, stops, and prints what to do: look in the
Webull app, then `aperture clear-fence <that fence>` and `aperture exit`. While a not-filled
close is still working at Webull, the bot leaves it alone (docs/operations.md, `exit-not-filled`).

`resume` and `exit` use today's flatten time from the calendar; after the close, on a
non-trading day, or if the calendar cannot be read, they close open trades right away.

Ctrl-C once: close through the normal exit path. Ctrl-C twice, closing the terminal, or a
SIGTERM: the command stops and the stop stays resting at the broker, with the position still
open. Run `aperture resume` (keeps protecting and closes at the flatten time) or `aperture exit`
(closes now) afterwards; the position never closes by itself while no command runs, except by
its stop.

Entries stay blocked while any fence is set (printed at the end of every command). Operator
fences need a look and are cleared with `aperture clear-fence NAME`: `stop-rejected:*`,
`exit-refused:*`, `exit-not-filled:*`, `replace-unresolved:*`, `cancel-failed:*`,
`resurrected:*`, `unprotected-timeout:*`. All others clear themselves, including
`stop-held:*` (see F5 results).

## Probe day (Monday 2026-11-16)

Goal: answer the open broker facts in docs/webull-facts.md with read-only calls and two
1-share protected trades. Times are New York (ET). Save every output under
`data/probe-2026-11-16/` (`data/` is never committed) and paste the files into the project
chat at the end; an agent then writes webull-facts.md and the fixtures from them.

### Before the open

1. The manual commands and the bot service share one lock and one database: `aperture trade`
   refuses while `aperture run` holds the lock. If the launchd jobs are installed, the bot job
   starts at `[runtime] bot_start_local` (Mac local time). For a probe day, stop it once it is
   up: `launchctl kill TERM gui/$(id -u)/local.aperture.bot`, then wait for the
   `"record": "bot-exit"` line in `~/Library/Logs/Aperture/bot.log`. Keep the watchdog job
   running (read-only, it checks you are flat after the flatten time). While a manual trade is
   open it alerts `bot-down-with-open-trade` ("no live pid in its lock file" or a stale status
   file): expected on a probe day, because the manual command is not the bot.
2. Terminal A, as in one-time setup: `cd`, `source`, load the env file, `aperture check-config`.
   Then `aperture status`: it must show `bot: not running`. (`status` does not list fences;
   `trade`, `exit` and `resume` print every fence still set when they end.)
3. Save the read script (once; it only reads, sends no orders, and prints raw bodies with the
   account id replaced by `<account>`). Output goes to the screen and, through `tee -i`, to a
   file (`-i`: a Ctrl-C reaches only the command, never the file writer):

```
mkdir -p data/probe-2026-11-16
cat > data/probe_reads.py <<'EOF'
import json, sys, time
from collections import namedtuple
from datetime import UTC, datetime
from aperture import config
from aperture.broker.webull import Route, SdkTransport, settings_from_config

cfg = config.load("config/bot.toml")
transport = SdkTransport(settings_from_config(cfg))
category = cfg.market_data.screener_category
TRADE_INSTRUMENT = namedtuple("R", "method path mutating")("GET", "/trade/instrument", False)

def show(label, route, params):
    response = transport.send(route, params)
    text = json.dumps(response.body, default=str, indent=1, sort_keys=True)
    print(f"--- {label}: HTTP {response.status}\n{transport._scrub(text)}", flush=True)
    return response.body

mode, args = sys.argv[1], sys.argv[2:]
if mode == "raw":    # raw SYMBOL... [order=CLIENT_ORDER_ID...]
    symbols = ",".join(a for a in args if not a.startswith("order="))
    show("snapshot", Route.SNAPSHOT, {"symbols": symbols, "category": category})
    body = show("profiles", Route.PROFILES, {"symbols": symbols, "category": category})
    for row in body if isinstance(body, list) else (body or {}).get("data", []):
        show(f"trade instrument {row['symbol']} {row['instrument_id']}", TRADE_INSTRUMENT,
             {"instrument_id": row["instrument_id"]})
    show("positions", Route.POSITIONS, {})
    show("open orders", Route.OPEN_ORDERS, {})
    for a in args:
        if a.startswith("order="):
            show(f"detail {a[6:]}", Route.ORDER_DETAIL, {"client_order_id": a[6:]})
elif mode == "watch":    # watch SYMBOL: open orders every 5 s for 5 minutes
    for _ in range(60):
        now = datetime.now(UTC).strftime("%H:%M:%S")
        try:
            response = transport.send(Route.OPEN_ORDERS, {})
            body = response.body if isinstance(response.body, dict) else {}
            rows = [o for combo in body.get("data", []) for o in combo.get("orders", [])
                    if o.get("symbol") == args[0]]
            print(now, "UTC HTTP", response.status, len(rows), "open:", "; ".join(
                f"{o.get('client_order_id')} {o.get('order_type')} {o.get('status')} "
                f"stop={o.get('stop_price')}" for o in rows), flush=True)
        except Exception as exc:
            print(now, "UTC read failed:", type(exc).__name__, flush=True)
        time.sleep(5)
EOF
```

Pick the trade symbol now: a liquid common stock priced under 50 USD (`[probe] max_price_usd`).
Below, `SYM` is that symbol.

### Step 0, right after 09:30: are Sandbox quotes real time?

```
aperture check-quote SYM 2>&1 | tee -i data/probe-2026-11-16/0-check-quote.txt
```

It reads one snapshot quote through the same path as `probe-replace-stop` and sends no orders.
It prints `SYM: bid B ask A, provider time T, age N s, limit 5 s`, then:

- `OK: quote is N s old (limit 5 s)` (exit 0): quotes are fresh enough; go on.
- `STALE: quote is N s old ...: the probes will refuse; the quotes may be delayed` (exit 3), or
  `refused: quote read failed: ...` / `refused: no quote for SYM` on stderr (exit 3): run it
  once more a minute later on another liquid symbol. If it still says STALE (an age near 900 s
  means the 15-minute delay), skip step 3 (F5 refuses on a stale quote). Steps 1, 2 and 4
  still run; take the step 2 limit price from the Webull app.

Answers: "whether our Sandbox quotes are real time" (Market data entitlement).

### Step 1, after 09:30: tradability and halts (read-only)

Look up a currently halted US stock on the Nasdaq trade-halts page
(nasdaqtrader.com, "Trade Halts"), if any; call it `HALTED`. Then:

```
.venv/bin/python data/probe_reads.py raw SYM AAPL OPENW HALTED 2>&1 | tee -i data/probe-2026-11-16/1-tradability.txt
```

(Leave `HALTED` out if nothing is halted.) What each block answers:

- `snapshot`: `trade_status` and `list_status` per symbol, and whether a halted symbol still
  has `bid`/`ask`/`quote_time` (a row without them gives the bot no quote).
- `profiles`: `status` per row (seen OC, CO, NT; what does a halted stock show?) and the two
  OPENW rows.
- `trade instrument <symbol> <id>`: `GET /trade/instrument` per instrument id:
  `trade_policy` (ALL / CLOSE_ONLY / DENIED per the SDK) and `forbid_reason`, or the error
  Sandbox gives for this route. Never read by Aperture before.
- `positions`, `open orders`: should be empty now (`{"data": []}`) before any trade.

Answers backlog "Tradability check before entry" (how a halt or a non-tradable instrument shows)
and the meaning of profile `status`.

### Step 2, 10:00 or later: one protected trade (F1, F2, F4, F6, F9, new-stop listing delay)

Read the ask (from step 0, or the app); the limit is ask + 0.01 (owner decision 2026-11-14).

1. Terminal B (set up as in one-time setup), start the open-orders watcher first:
   `.venv/bin/python data/probe_reads.py watch SYM 2>&1 | tee -i data/probe-2026-11-16/2-watch.txt`
2. Terminal A: `aperture trade SYM --limit LIMIT 2>&1 | tee -i data/probe-2026-11-16/2-trade.txt`
3. While the terminal is in the hold (the app shows the working SELL stop; you have 60 s),
   Terminal C: `.venv/bin/python data/probe_reads.py raw SYM order=<stop id> 2>&1 | tee -i data/probe-2026-11-16/2-held.txt`.
   The stop id is the stop's client order id shown in the watcher lines (it starts with `ap-`).
   This records a non-empty positions body, an open-orders body with a working STOP_LOSS
   (is `stop_price` named so?) and the stop's raw Detail.
4. After the trade prints `trade <id> SYM: closed (...)`, the journal (UTC times; the last
   column is the leg of each `order-intent`):
   `sqlite3 data/aperture.sqlite3 "SELECT at_utc, kind, key, json_extract(body, '$.leg') FROM journal WHERE at_utc >= '2026-11-16' ORDER BY seq" | tee data/probe-2026-11-16/2-journal.txt`
5. Raw Detail of the three orders (ids are the `order-intent` keys: entry, stop, exit):
   `.venv/bin/python data/probe_reads.py raw SYM order=<entry id> order=<stop id> order=<exit id> 2>&1 | tee -i data/probe-2026-11-16/2-after.txt`.
   Fill shape, average price, fees field (if any), the cancelled stop, the filled MARKET SELL.

The new-stop listing delay is the first watcher line that lists the stop id minus the stop's
`order-intent` time in the journal (5 s resolution). The cancel of the working stop (F4) is the
watcher line where it disappears.

### Step 3: probe F5, replace the stop

Goal: learn how Webull replaces the price of a working STOP_LOSS (docs/webull-facts.md, F5),
so the trailing stop can be built. One replace, tightening only, on the bot's own trade. Skip it
if step 0 said STALE.

1. Terminal A: `aperture trade SYM --limit LIMIT 2>&1 | tee -i data/probe-2026-11-16/3-trade.txt`.
   Wait until the app shows the working SELL stop and the terminal is in the hold
   (`hold_seconds`, 60 s, from the moment the stop is seen working: the kill in the next item
   must happen inside it).
2. Terminal B (the step 2 watcher has ended): `aperture status` (it prints
   `bot: pid <pid> recorded ...`), then `kill -TERM <pid>`. The trade command stops; the position and its stop stay at the broker.
   Do not use Ctrl-C (the first one asks for an exit).
3. Terminal B: start the watcher:
   `.venv/bin/python data/probe_reads.py watch SYM 2>&1 | tee -i data/probe-2026-11-16/3-watch.txt`
4. Look up the bid in the app. Pick a new stop above the current stop, below the bid, at most
   `[probe] replace_max_move_usd` (0.50) above the current stop, whole cents.
5. Terminal A: `aperture probe-replace-stop --to NEW_STOP 2>&1 | tee -i data/probe-2026-11-16/3-replace.txt`.
   It refuses (exit 3, nothing sent) unless: gate open, inside the probe window, exactly one
   open trade with no exit requested, no fence, the broker position equals our records, one
   verified working unfilled stop, a quote no older than `max_quote_age_seconds`, and the
   price rules above. Then it journals the intent and sets the operator fence
   `replace-unresolved:<stop id>`, sends ONE replace, polls Detail for
   `replace_observe_seconds` (15 s), reads open orders once, and prints all of it:
   - `replace answer: ...` (success / unknown / not present, echoed ids);
   - every `Detail:` line: client_order_id, order_id, status, stop_price (or "not shown");
   - every `open order:` line;
   - `F5 result: ...`. Screenshot the app's order list too (same order edited, or a new
     order; price; still working throughout?).
6. Close: `aperture exit 2>&1 | tee -i data/probe-2026-11-16/3-exit.txt` (read the F5
   results below first if the result is not `confirmed`). Check in the app: no position, no working orders.
7. Journal again, as in step 2.4, into `3-journal.txt`.

The replace listing delay is the watcher: the time from the `replace-intent` row in the journal
until the watcher shows the new price (same id) or a new SELL stop id. The bot's stop hold after
an unresolved replace (`stop-held`) waits for two clean open-orders reads
`reconcile_interval_seconds` (10 s) apart, assuming Webull lists a new order within that time.

F5 results:
- `F5 result: confirmed` (exit 0): same client_order_id, working at the new price. Fence
  cleared; our record carries the new price.
- `F5 result: adopted` (exit 4): Webull made a NEW order at the new price and the old one ended.
  The bot now tracks the new order as the trade's stop, so `aperture exit` cancels it. Check the
  app shows exactly one working stop, then `aperture clear-fence replace-unresolved:<stop id>`.
- `F5 result: not-sent` (exit 3): refused before or by the broker; nothing changed.
- `F5 result: unresolved` (exit 4), including an unknown outcome: the command never resends.
  Do NOT run another replace. Look in the app: count the working SELL stops in the symbol.
  - Exactly one, ours, any price: `aperture exit` closes normally (it cancels our stop id).
  - None: the position is unprotected; run `aperture exit` at once.
  - A stop with an id that is not ours (the bot then reports `foreign-order:<id>`), or more
    than one: cancel the extra stops in the app and close the position there, then
    `aperture status`; send the screenshots before running anything else.
  Clear `replace-unresolved:<stop id>` only after the app shows a clean state.

  What the bot does meanwhile (`aperture exit` or `resume` running): while
  `replace-unresolved:<stop id>` is set and the bot would place a stop or send a close, it
  reads open orders itself, at most once per `reconcile_interval_seconds`. It places no stop
  and sends no close until two such reads in a row show no working SELL in the symbol that is
  not ours (Webull may list a new order a moment late); a failed read holds too. The terminal
  prints `needs the operator: stop-held:<trade>: ...` once, with the order id when one is seen
  ("stop and close held: untracked SELL <id> rests in ABC after an unresolved replace; resolve
  it in the Webull app"); otherwise it may only say "exiting". Once you cancel that order in
  the app, two clean reads (about 10-20 s) release the hold and, because the exit is already
  requested (by `aperture exit`, or by the unprotected-too-long rule), the bot sells the
  position at MARKET. It does not place a new stop first. In the exit path a release is good
  for one pass only: after it the bot cancels our stop and then holds again for two reads
  (about 10 to 20 s) with no stop of ours before the MARKET close. This is expected and does
  not mean the bot is stuck.

### Step 4: record the open broker facts

Each open fact in docs/webull-facts.md, the output that answers it, and its file:

- Sandbox quotes real time or delayed: step 0 `age` (`0-check-quote.txt`).
- A raw snapshot during the regular session: `1-tradability.txt`.
- Halt / non-tradable instrument: snapshot `trade_status`, `list_status`, missing bid/ask;
  profile `status` (OC/CO/NT and a halted stock); `/trade/instrument` `trade_policy`,
  `forbid_reason` (`1-tradability.txt`).
- F1 fill and its Detail shape, fees: `order=<entry id>` Detail (`2-after.txt`).
- F2 STOP_LOSS SELL accepted and listed in RTH, `stop_price` field name: `2-held.txt`,
  `2-watch.txt`.
- New-stop listing delay: first `2-watch.txt` line with the stop id minus its `order-intent`
  time (`2-journal.txt`).
- F4 cancel of a working stop: the stop's last `2-watch.txt` line and its Detail in
  `2-after.txt`.
- F6 MARKET SELL in RTH: the exit's Detail (`2-after.txt`).
- F9 non-empty positions and open-orders shapes: `positions` / `open orders` in `2-held.txt`.
- F5 replace, and the replaced-stop listing delay: `3-replace.txt`, `3-watch.txt` against the
  `replace-intent` time in `3-journal.txt`, and the app screenshots.

Then hand over: the last `aperture exit` printed no `fence ...` line, `aperture status` shows
`bot: not running`, and the app shows no position and no working order. Paste every file in
`data/probe-2026-11-16/` and the screenshots into the project chat. Before pasting, check that
nothing in them identifies the account (the script replaces only the account id). If you
stopped the bot job in "Before the open", it starts again on its own the next weekday.
