# Manual protected trade (stage 3)

Run on the owner's Mac, in market hours, in Sandbox (`[run] environment` in `config/bot.toml`).
Credentials are read from the environment variables named in the active `[environments.*]` table.

```
aperture check-config
aperture trade SYMBOL --limit 12.34            # quantity and stop default from [probe]
aperture trade SYMBOL --limit 12.34 --stop 12.10
```

What `trade` does: refuses outside the regular session, outside `[probe] window_start..window_end`
or after the last-entry time; refuses a price above `max_price_usd` or a planned loss above
`max_loss_usd`. Then: LIMIT BUY (DAY, core session) -> on every observed fill a SELL STOP_LOSS for
exactly that quantity -> once the stop is seen working, hold `hold_seconds` -> cancel the stop,
confirm it is cancelled -> MARKET SELL exactly the held quantity -> closed when the broker is flat.
An entry that does not fill within `entry_wait_seconds` is cancelled.

Ctrl-C once: close through the normal exit path. Ctrl-C twice: stop the command and leave the
stop resting at the broker.

After a crash or `kill -9`:

```
aperture resume      # recover, reconcile, keep protecting; ends when no trade is open
aperture exit        # recover, then close every open trade through the normal exit path
aperture status
```

Entries stay blocked while any fence is set (printed at the end of every command). Fences that
need a look (`stop-rejected:*`, `exit-refused:*`) are cleared with
`aperture clear-fence NAME`; all others clear themselves.
