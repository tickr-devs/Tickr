/// SF Symbols of the live flow's stages, as on the Overview board: one source for both apps, line
/// style (no fills), drawn at the stage's symbol font (`Tokens.Typography.stageSymbol`).
public enum FlowStageSymbols {
  /// Screeners: a list under a magnifier, the symbols the screeners saw.
  public static let screenersSymbol = "text.magnifyingglass"
  /// Universe: lines narrowing down, the screened symbols filtered to tradable stocks.
  public static let universeSymbol = "line.3.horizontal.decrease"
  /// Strategy: a branch, the strategy deciding which way a setup goes.
  public static let strategySymbol = "arrow.triangle.branch"
  /// Risk: a gauge, the room and limits each entry is measured against.
  public static let riskSymbol = "gauge.with.needle"
  /// Order: a paper plane, the entry sent to the broker once.
  public static let orderSymbol = "paperplane"
  /// Broker stop: a shield with a check, the stop verified at the broker.
  public static let stopSymbol = "checkmark.shield"
  /// Reconcile: two arrows going round, the bot's records read against the broker's.
  public static let reconcileSymbol = "arrow.triangle.2.circlepath"
}
