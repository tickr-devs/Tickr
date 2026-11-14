import Foundation

// Generated from strings.py while the screens wait for the macOS L10n.swift.
extension L10n {
  enum Chart {
    /// Label of the chart's symbol picker.
    static var symbol: LocalizedStringResource { LocalizedStringResource("chart.symbol", defaultValue: "Symbol") }
    /// Label of the timeframe picker.
    static var timeframeLabel: LocalizedStringResource { LocalizedStringResource("chart.timeframeLabel", defaultValue: "Timeframe") }
    /// A timeframe segment: candle length in minutes.
    static func timeframe(_ minutes: Int) -> LocalizedStringResource {
      LocalizedStringResource("chart.timeframe", defaultValue: "M\(minutes)")
    }
    /// Tooltip of the timeframe picker.
    static var timeframeHelp: LocalizedStringResource { LocalizedStringResource("chart.timeframeHelp", defaultValue: "Candle length in minutes") }
    /// The VWAP line toggle and readout.
    static var vwap: LocalizedStringResource { LocalizedStringResource("chart.vwap", defaultValue: "VWAP") }
    /// An EMA line toggle and readout.
    static func ema(_ period: Int) -> LocalizedStringResource {
      LocalizedStringResource("chart.ema", defaultValue: "EMA \(period)")
    }
    /// The volume pane toggle.
    static var volume: LocalizedStringResource { LocalizedStringResource("chart.volume", defaultValue: "Volume") }
    /// Readout label of a candle's volume.
    static var volumeShort: LocalizedStringResource { LocalizedStringResource("chart.volumeShort", defaultValue: "V") }
    /// The ATR pane toggle and readout.
    static func atr(_ period: Int) -> LocalizedStringResource {
      LocalizedStringResource("chart.atr", defaultValue: "ATR \(period)")
    }
    /// ATR pane before the line has a value.
    static func atrWaiting(_ period: Int) -> LocalizedStringResource {
      LocalizedStringResource("chart.atrWaiting", defaultValue: "ATR \(period): not enough candles yet")
    }
    /// Zoom-in button.
    static var zoomIn: LocalizedStringResource { LocalizedStringResource("chart.zoomIn", defaultValue: "Zoom In") }
    /// Zoom-out button.
    static var zoomOut: LocalizedStringResource { LocalizedStringResource("chart.zoomOut", defaultValue: "Zoom Out") }
    /// Button that shows the whole day.
    static var fit: LocalizedStringResource { LocalizedStringResource("chart.fit", defaultValue: "Show Whole Day") }
    /// Tooltip of zoom in.
    static var zoomInHelp: LocalizedStringResource { LocalizedStringResource("chart.zoomInHelp", defaultValue: "Show fewer candles (⌘=)") }
    /// Tooltip of zoom out.
    static var zoomOutHelp: LocalizedStringResource { LocalizedStringResource("chart.zoomOutHelp", defaultValue: "Show more candles (⌘−)") }
    /// Tooltip of show whole day.
    static var fitHelp: LocalizedStringResource { LocalizedStringResource("chart.fitHelp", defaultValue: "Show the whole day (⌘0)") }
    /// Empty chart: no symbol has data.
    static var noSymbols: LocalizedStringResource { LocalizedStringResource("chart.noSymbols", defaultValue: "Nothing to Chart") }
    /// Empty chart detail.
    static var noSymbolsDetail: LocalizedStringResource { LocalizedStringResource("chart.noSymbolsDetail", defaultValue: "The bot has no open position, recorded bars or trades on this day.") }
    /// The bars read failed.
    static var barsUnavailable: LocalizedStringResource { LocalizedStringResource("chart.barsUnavailable", defaultValue: "Bars Unavailable") }
    /// Why the bars read failed.
    static func barsUnavailableDetail(_ cause: String) -> LocalizedStringResource {
      LocalizedStringResource("chart.barsUnavailableDetail", defaultValue: "The bot's API did not return bars: \(cause)")
    }
    /// While bars load.
    static var loading: LocalizedStringResource { LocalizedStringResource("chart.loading", defaultValue: "Loading Bars…") }
    /// The symbol has no bars on the day.
    static var noBars: LocalizedStringResource { LocalizedStringResource("chart.noBars", defaultValue: "No Bars") }
    /// No bars detail.
    static var noBarsDetail: LocalizedStringResource { LocalizedStringResource("chart.noBarsDetail", defaultValue: "No M1 bars were recorded for this symbol on this day.") }
    /// Entry mark of a trade with its quantity.
    static func buyMark(_ quantity: String) -> LocalizedStringResource {
      LocalizedStringResource("chart.buyMark", defaultValue: "Buy \(quantity)")
    }
    /// Exit mark of a trade with its result.
    static func exitMark(_ result: String) -> LocalizedStringResource {
      LocalizedStringResource("chart.exitMark", defaultValue: "Exit \(result)")
    }
    /// Entry level of the open position.
    static func entryLevel(_ price: String) -> LocalizedStringResource {
      LocalizedStringResource("chart.entryLevel", defaultValue: "Entry \(price)")
    }
    /// Verified broker-side stop level.
    static func stopLevel(_ price: String) -> LocalizedStringResource {
      LocalizedStringResource("chart.stopLevel", defaultValue: "Stop \(price) ✓")
    }
    /// Stop level the broker has not confirmed.
    static func stopUnverified(_ price: String) -> LocalizedStringResource {
      LocalizedStringResource("chart.stopUnverified", defaultValue: "Stop \(price) unverified")
    }
    /// Readout: open.
    static var open: LocalizedStringResource { LocalizedStringResource("chart.open", defaultValue: "O") }
    /// Readout: high.
    static var high: LocalizedStringResource { LocalizedStringResource("chart.high", defaultValue: "H") }
    /// Readout: low.
    static var low: LocalizedStringResource { LocalizedStringResource("chart.low", defaultValue: "L") }
    /// Readout: close.
    static var close: LocalizedStringResource { LocalizedStringResource("chart.close", defaultValue: "C") }
    /// Plotted value name (VoiceOver).
    static var axisTime: LocalizedStringResource { LocalizedStringResource("chart.axisTime", defaultValue: "Time") }
    /// Plotted value name (VoiceOver).
    static var axisPrice: LocalizedStringResource { LocalizedStringResource("chart.axisPrice", defaultValue: "Price") }
    /// Plotted series name (VoiceOver).
    static var axisSeries: LocalizedStringResource { LocalizedStringResource("chart.axisSeries", defaultValue: "Line") }
  }
  enum Trades {
    /// Card title: the list of recorded days.
    static var days: LocalizedStringResource { LocalizedStringResource("trades.days", defaultValue: "Days") }
    /// Days list is empty.
    static var noDays: LocalizedStringResource { LocalizedStringResource("trades.noDays", defaultValue: "No recorded days yet.") }
    /// Days read failed.
    static var daysUnavailable: LocalizedStringResource { LocalizedStringResource("trades.daysUnavailable", defaultValue: "The day list is unavailable; the bot's API did not answer.") }
    /// Plotted value name of a day bar (VoiceOver).
    static var day: LocalizedStringResource { LocalizedStringResource("trades.day", defaultValue: "Day") }
    /// A trade's or day's gross result.
    static var result: LocalizedStringResource { LocalizedStringResource("trades.result", defaultValue: "Result") }
    /// Trades of a day with wins and losses.
    static func closedWinsLosses(_ trades: Int, _ wins: Int, _ losses: Int) -> LocalizedStringResource {
      LocalizedStringResource("trades.closedWinsLosses", defaultValue: "\(trades) · \(wins) W · \(losses) L")
    }
    /// Tooltip: a day without bars.
    static var noBarsHelp: LocalizedStringResource { LocalizedStringResource("trades.noBarsHelp", defaultValue: "No bars were recorded on this day, so the Chart has nothing to show.") }
    /// Stat card: the day's gross result.
    static var resultGross: LocalizedStringResource { LocalizedStringResource("trades.resultGross", defaultValue: "Result · gross") }
    /// Stat card: number of trades.
    static var trades: LocalizedStringResource { LocalizedStringResource("trades.trades", defaultValue: "Trades") }
    /// Stat card: average result in R.
    static var averageR: LocalizedStringResource { LocalizedStringResource("trades.averageR", defaultValue: "Average R") }
    /// Stat card: best and worst trade.
    static var bestWorst: LocalizedStringResource { LocalizedStringResource("trades.bestWorst", defaultValue: "Best · worst") }
    /// Card title: the day's trade table.
    static var tradesOfDay: LocalizedStringResource { LocalizedStringResource("trades.tradesOfDay", defaultValue: "Closed trades") }
    /// Trade table empty on the live day.
    static var noTradesToday: LocalizedStringResource { LocalizedStringResource("trades.noTradesToday", defaultValue: "No closed trades yet today.") }
    /// Trade table empty on a recorded day.
    static var noTrades: LocalizedStringResource { LocalizedStringResource("trades.noTrades", defaultValue: "No closed trades on this day.") }
    /// Column: time the trade closed (New York).
    static var closed: LocalizedStringResource { LocalizedStringResource("trades.closed", defaultValue: "Closed") }
    /// Column: symbol.
    static var symbol: LocalizedStringResource { LocalizedStringResource("trades.symbol", defaultValue: "Symbol") }
    /// Column: shares.
    static var quantity: LocalizedStringResource { LocalizedStringResource("trades.quantity", defaultValue: "Qty") }
    /// Column: entry price.
    static var entry: LocalizedStringResource { LocalizedStringResource("trades.entry", defaultValue: "Entry") }
    /// Column: exit price.
    static var exit: LocalizedStringResource { LocalizedStringResource("trades.exit", defaultValue: "Exit") }
    /// Column: stop price.
    static var stop: LocalizedStringResource { LocalizedStringResource("trades.stop", defaultValue: "Stop") }
    /// Column: exit reason.
    static var reason: LocalizedStringResource { LocalizedStringResource("trades.reason", defaultValue: "Reason") }
    /// Column: result in R.
    static var resultR: LocalizedStringResource { LocalizedStringResource("trades.resultR", defaultValue: "R") }
    /// Context menu: open the trade's symbol on the Chart.
    static var showOnChart: LocalizedStringResource { LocalizedStringResource("trades.showOnChart", defaultValue: "Show on Chart") }
    /// Card title: what-if replays.
    static var whatIf: LocalizedStringResource { LocalizedStringResource("trades.whatIf", defaultValue: "What if") }
    /// What-if card text.
    static var whatIfDetail: LocalizedStringResource { LocalizedStringResource("trades.whatIfDetail", defaultValue: "Replay a skipped signal as if the bot had entered, or the day under another strategy.") }
    /// Planned button: replay a skipped signal.
    static var whatIfEntered: LocalizedStringResource { LocalizedStringResource("trades.whatIfEntered", defaultValue: "If We Had Entered…") }
    /// Planned button: replay with another strategy.
    static var whatIfStrategy: LocalizedStringResource { LocalizedStringResource("trades.whatIfStrategy", defaultValue: "Another Strategy…") }
  }
  enum Risk {
    /// Card title: room left before the daily loss limit.
    static var lossRoom: LocalizedStringResource { LocalizedStringResource("risk.lossRoom", defaultValue: "Daily loss room · net") }
    /// Under the ring: room left.
    static var roomLeft: LocalizedStringResource { LocalizedStringResource("risk.roomLeft", defaultValue: "Left today") }
    /// The configured daily loss limit.
    static var limit: LocalizedStringResource { LocalizedStringResource("risk.limit", defaultValue: "Daily loss limit") }
    /// Today's net result.
    static var resultToday: LocalizedStringResource { LocalizedStringResource("risk.resultToday", defaultValue: "Result today · net") }
    /// How the daily limit works.
    static var lossRoomNote: LocalizedStringResource { LocalizedStringResource("risk.lossRoomNote", defaultValue: "Profits offset losses. Near the limit the bot sizes trades down to the room left; with almost no room it stops entering.") }
    /// Card title: may the bot enter now.
    static var entryGate: LocalizedStringResource { LocalizedStringResource("risk.entryGate", defaultValue: "New entries") }
    /// Badge: entries allowed.
    static var allowed: LocalizedStringResource { LocalizedStringResource("risk.allowed", defaultValue: "ALLOWED") }
    /// Badge: entries blocked.
    static var blocked: LocalizedStringResource { LocalizedStringResource("risk.blocked", defaultValue: "BLOCKED") }
    /// Entry stop set by the operator.
    static var operatorPause: LocalizedStringResource { LocalizedStringResource("risk.operatorPause", defaultValue: "Operator pause") }
    /// The configured trading gate.
    static var tradingGate: LocalizedStringResource { LocalizedStringResource("risk.tradingGate", defaultValue: "Trading gate") }
    /// Trading gate open.
    static var gateOpen: LocalizedStringResource { LocalizedStringResource("risk.gateOpen", defaultValue: "open") }
    /// Trading gate closed.
    static var gateClosed: LocalizedStringResource { LocalizedStringResource("risk.gateClosed", defaultValue: "closed") }
    /// Card title: blocks the bot raised itself.
    static var fences: LocalizedStringResource { LocalizedStringResource("risk.fences", defaultValue: "Fences") }
    /// No fences.
    static var noFences: LocalizedStringResource { LocalizedStringResource("risk.noFences", defaultValue: "No fences: nothing the bot noticed blocks entries.") }
    /// A fence's kind and start time (New York).
    static func fenceSince(_ kind: String, _ time: String) -> LocalizedStringResource {
      LocalizedStringResource("risk.fenceSince", defaultValue: "\(kind) · since \(time)")
    }
    /// Card title: configured risk limits.
    static var limits: LocalizedStringResource { LocalizedStringResource("risk.limits", defaultValue: "Limits") }
    /// Configured planned loss.
    static var plannedLoss: LocalizedStringResource { LocalizedStringResource("risk.plannedLoss", defaultValue: "Planned loss per trade (1 R)") }
    /// Configured notional cap.
    static var notionalCap: LocalizedStringResource { LocalizedStringResource("risk.notionalCap", defaultValue: "Position size cap") }
    /// Configured daily loss limit.
    static var dailyLoss: LocalizedStringResource { LocalizedStringResource("risk.dailyLoss", defaultValue: "Daily loss limit") }
    /// Configured position count.
    static var openPositions: LocalizedStringResource { LocalizedStringResource("risk.openPositions", defaultValue: "Open positions at most") }
    /// Configured quote age.
    static var quoteAge: LocalizedStringResource { LocalizedStringResource("risk.quoteAge", defaultValue: "Oldest quote to trade on") }
    /// A duration in seconds.
    static func seconds(_ value: String) -> LocalizedStringResource {
      LocalizedStringResource("risk.seconds", defaultValue: "\(value) s")
    }
    /// Settings read failed.
    static var settingsUnavailable: LocalizedStringResource { LocalizedStringResource("risk.settingsUnavailable", defaultValue: "Settings are unavailable; the bot's API did not answer.") }
    /// Card title: the trading session.
    static var session: LocalizedStringResource { LocalizedStringResource("risk.session", defaultValue: "Session · New York") }
    /// Last entry time.
    static var lastEntry: LocalizedStringResource { LocalizedStringResource("risk.lastEntry", defaultValue: "Last entry") }
    /// Flatten time.
    static var flatten: LocalizedStringResource { LocalizedStringResource("risk.flatten", defaultValue: "Close all") }
    /// Half-day handling.
    static var halfDays: LocalizedStringResource { LocalizedStringResource("risk.halfDays", defaultValue: "Half days") }
    /// Minutes before the close.
    static func minutesBeforeClose(_ minutes: Int) -> LocalizedStringResource {
      LocalizedStringResource("risk.minutesBeforeClose", defaultValue: "\(minutes) min before close")
    }
    /// Card title: broker account.
    static var account: LocalizedStringResource { LocalizedStringResource("risk.account", defaultValue: "Account") }
    /// Account equity.
    static var equity: LocalizedStringResource { LocalizedStringResource("risk.equity", defaultValue: "Equity") }
    /// Account buying power.
    static var buyingPower: LocalizedStringResource { LocalizedStringResource("risk.buyingPower", defaultValue: "Buying power") }
    /// Positions the broker reports.
    static var brokerPositions: LocalizedStringResource { LocalizedStringResource("risk.brokerPositions", defaultValue: "Broker positions") }
    /// Orders the broker reports.
    static var brokerOrders: LocalizedStringResource { LocalizedStringResource("risk.brokerOrders", defaultValue: "Broker orders") }
    /// Why the account card is empty.
    static var accountNote: LocalizedStringResource { LocalizedStringResource("risk.accountNote", defaultValue: "Shown once the bot's API serves the broker's account read.") }
  }
  enum Lab {
    /// Card title: the strategy the bot trades with.
    static var activeStrategy: LocalizedStringResource { LocalizedStringResource("lab.activeStrategy", defaultValue: "Active strategy") }
    /// The shown day's trades.
    static var shownDayTrades: LocalizedStringResource { LocalizedStringResource("lab.shownDayTrades", defaultValue: "Trades") }
    /// Trades with wins and losses.
    static func closedWinsLosses(_ trades: Int, _ wins: Int, _ losses: Int) -> LocalizedStringResource {
      LocalizedStringResource("lab.closedWinsLosses", defaultValue: "\(trades) · \(wins) W · \(losses) L")
    }
    /// The shown day's gross result.
    static var shownDayResult: LocalizedStringResource { LocalizedStringResource("lab.shownDayResult", defaultValue: "Result · gross") }
    /// How strategies are swapped.
    static var swapNote: LocalizedStringResource { LocalizedStringResource("lab.swapNote", defaultValue: "Strategies are swappable modules: the active one is set in the bot's config and never loosens platform risk.") }
    /// Card title: shadow strategies.
    static var observers: LocalizedStringResource { LocalizedStringResource("lab.observers", defaultValue: "Observers") }
    /// What observers do.
    static var observersNote: LocalizedStringResource { LocalizedStringResource("lab.observersNote", defaultValue: "Shadow versions see the same data live and record their decisions without trading.") }
    /// Card title: replaying recorded days.
    static var replay: LocalizedStringResource { LocalizedStringResource("lab.replay", defaultValue: "Replay") }
    /// What replay does.
    static var replayNote: LocalizedStringResource { LocalizedStringResource("lab.replayNote", defaultValue: "Replay a recorded day: skipped signals as if the bot had entered, or the whole day under another strategy.") }
    /// Planned button: replay a day.
    static var replayDay: LocalizedStringResource { LocalizedStringResource("lab.replayDay", defaultValue: "Replay Day") }
    /// Planned button: skipped signals.
    static var skippedSignals: LocalizedStringResource { LocalizedStringResource("lab.skippedSignals", defaultValue: "Skipped Signals") }
    /// Planned button: compare strategies.
    static var compare: LocalizedStringResource { LocalizedStringResource("lab.compare", defaultValue: "Compare") }
    /// Card title: the AI advisor.
    static var advisor: LocalizedStringResource { LocalizedStringResource("lab.advisor", defaultValue: "Advisor") }
    /// What the advisor does.
    static var advisorNote: LocalizedStringResource { LocalizedStringResource("lab.advisorNote", defaultValue: "Proposes new strategy versions from recorded days, with the evidence, for you to accept or reject.") }
  }
}
