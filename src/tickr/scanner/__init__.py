"""Shadow Massive scanner: the gainers, losers and volume lists built from Massive minute bars
with the research definition (`scanner.lists`), live (`scanner.run`) and from archived flat files
(`scanner.replay`). An observer only: it never reads or writes the trading database, never talks
to the broker and runs in its own process (docs/scanner.md)."""
