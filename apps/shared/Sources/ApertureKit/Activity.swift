import Foundation

extension StatusV1 {
  /// What the bot has done, without its clock: the status minus `writtenAt` and the freshness
  /// figures, which change on every write. It changes only on a real event (state, gate, entry
  /// stop or block, fences, a position opened, moved or closed, a trade, a command), so the apps
  /// animate on it and stay still otherwise.
  public struct Activity: Hashable, Sendable {
    let botState: BotState
    let tradingGate: Gate
    let entryStop: Bool
    let entryBlock: EntryBlock?
    let fences: [Fence]
    let positions: [Position]
    let tradesToday: [Trade]
    let recentCommands: [CommandRecord]
  }

  public var activity: Activity {
    Activity(botState: botState, tradingGate: tradingGate, entryStop: entryStop, entryBlock: entryBlock,
      fences: fences, positions: positions, tradesToday: tradesToday, recentCommands: recentCommands)
  }
}
