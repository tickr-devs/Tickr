import Foundation

// Swift mirror of SimulationRulesV1 in src/tickr/api/contract.py (docs/api.md, "Simulation
// rules"): the live bot's platform rules a simulated answer was computed under. Carried by
// observers-v1, skipped-v1 and trade-whatif-v1; added after version 1 shipped, so an older
// bot's document has none.

/// The platform rules a simulation obeyed and the bot.toml values they read.
public struct SimulationRulesV1: Codable, Hashable, Sendable {
  /// The admission checks every simulated entry passed (`session`, `own_state`, `decision`,
  /// `money`).
  public let applied: [String]
  /// The checks a simulation has no counterpart for (`gate`, `reconciliation`, `quote`,
  /// `request_budget`, `buying_power`).
  public let notSimulated: [String]
  /// Every bot.toml value the applied checks read, by dotted key, as text.
  public let values: [String: String]

  public init(applied: [String], notSimulated: [String], values: [String: String]) {
    self.applied = applied
    self.notSimulated = notSimulated
    self.values = values
  }

  enum CodingKeys: String, CodingKey {
    case applied, values
    case notSimulated = "not_simulated"
  }
}
