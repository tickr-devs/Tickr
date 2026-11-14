# Observer strategies (shadow versions, never trade)

An observer is another strategy version that sees exactly what the active strategy sees and
whose decisions are only recorded. It cannot place, cancel or size an order and never reaches
the safety layer. Use it to compare a candidate version against the active one on live days;
the daily report then shows both side by side (`python -m aperture.research observers`).

## Configure

`config/bot.toml`:

```
[strategy]
active = "price-action-v1"
observers = ["price-action-v1-lookback8"]      # folders under strategies/

[observers]
strategy_modules = ["price-action-v1=aperture.strategy.price_action_v1"]
max_observers = 4
max_evaluation_seconds = 0.5
decisions_dir = "decisions"
```

Each observer folder holds a full `strategy.toml`. Its `[strategy] name` picks the module from
`strategy_modules`; its `version` makes the identity. A value variant of the active strategy is
a new folder with the same name and a new version (owner rule: changing a value means a new
version). Every identity (name + version) must be distinct, the active one included. Setup
problems are refused with one cause (`ObserverError`): missing folder, unknown module, values
refused by the strategy, duplicate identity, more than `max_observers`.

## Engine wiring (to do in the engine, not here)

```python
from aperture.observers.loader import load_observers
from aperture.observers.log import DecisionLog
from aperture.observers.runner import ObserverSet

observers = ObserverSet(load_observers(config), DecisionLog.from_config(config),
                        config.observers.max_evaluation_seconds)

# per evaluation, right after the active strategy decided on `context` at time `now`:
for failure in observers.on_evaluation(context, now):
    journal("observer-off", failure)      # and alert once
```

- Call it after the active decision has been handled, so observers never delay an entry.
- Pass the same `SymbolContext` the active strategy got. Observers get it without the position.
- The active decision should be written with the same `DecisionLog.append` (role `active`,
  admission `taken` / `refused` + cause, `not_applicable` for non-ENTER) so the daily report has
  both sides in one file.
- `on_evaluation` never raises for an observer fault. An exception, a contract break (wrong
  tag or symbol) or an evaluation slower than `max_evaluation_seconds` switches that observer
  off for the run and is returned as an `ObserverFailure`; a failed log write switches all of
  them off. It raises ValueError only for a caller bug (decision time before the bar ended).

Files: `<data_dir>/<decisions_dir>/<environment>/<NY date>/decisions.jsonl`, one decision record
per line (format in `docs/research.md`).

## Daily report

```
python -m aperture.research observers \
  --decisions data/decisions/sandbox/2026-11-17/decisions.jsonl \
  --bars data/capture/sandbox/2026-11-17/bars.jsonl --out out/observers-2026-11-17
```

Per identity (active first): decisions by verdict, ENTERs (taken / refused for the active),
every ENTER simulated like `whatif` (taken or not, so every version is measured the same way;
an ENTER while an earlier simulated trade of the same identity in that symbol is open is
`skipped_overlap`), trades, wins, losses, total and average R, gross USD for ENTERs with a
recorded quantity; for observers the bar-by-bar diff against the active decisions and the
number of ENTER bars shared with it. Outputs `observers.csv` and `observers.json`, labelled
`simulated`.
