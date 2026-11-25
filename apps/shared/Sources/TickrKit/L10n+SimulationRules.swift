import Foundation

/// Texts of the label under a simulated result: the platform rules it was computed under
/// (`SimulationRulesV1`, backlog T12), on both apps.
public enum SimulationRulesText {
  static var bundle: LocalizedStringResource.BundleDescription { L10n.bundle }

  /// Line under a simulated result: the rules and values it obeyed.
  public static func underRules(_ rules: String) -> LocalizedStringResource {
    LocalizedStringResource("kit.simRules.underRules",
      defaultValue: "Computed under the bot's rules: \(rules).", bundle: bundle)
  }
  /// Line under a simulated result whose values this app cannot name.
  public static var underRulesPlain: LocalizedStringResource {
    LocalizedStringResource("kit.simRules.underRulesPlain",
      defaultValue: "Computed under the bot's rules.", bundle: bundle)
  }
  /// What a simulation has no counterpart for.
  public static func notSimulated(_ checks: String) -> LocalizedStringResource {
    LocalizedStringResource("kit.simRules.notSimulated", defaultValue: "Not simulated: \(checks).", bundle: bundle)
  }
  /// Rule value: planned loss per trade (USD text).
  public static func plannedLoss(_ usd: String) -> LocalizedStringResource {
    LocalizedStringResource("kit.simRules.plannedLoss", defaultValue: "planned loss \(usd)", bundle: bundle)
  }
  /// Rule value: notional cap per position (USD text).
  public static func positionCap(_ usd: String) -> LocalizedStringResource {
    LocalizedStringResource("kit.simRules.positionCap", defaultValue: "position cap \(usd)", bundle: bundle)
  }
  /// Rule value: daily loss limit (USD text).
  public static func dailyLimit(_ usd: String) -> LocalizedStringResource {
    LocalizedStringResource("kit.simRules.dailyLimit", defaultValue: "daily loss limit \(usd)", bundle: bundle)
  }
  /// Rule value: most positions open at once.
  public static func openPositions(_ count: String) -> LocalizedStringResource {
    LocalizedStringResource("kit.simRules.openPositions", defaultValue: "up to \(count) positions", bundle: bundle)
  }
  /// Rule value: pause on a symbol after a loss, in minutes.
  public static func pauseAfterLoss(_ minutes: String) -> LocalizedStringResource {
    LocalizedStringResource("kit.simRules.pauseAfterLoss",
      defaultValue: "a \(minutes) min pause after a loss", bundle: bundle)
  }
  /// Rule value: the pause after a loss is off.
  public static var noPauseAfterLoss: LocalizedStringResource {
    LocalizedStringResource("kit.simRules.noPauseAfterLoss", defaultValue: "no pause after a loss", bundle: bundle)
  }
  /// Rule value: last entry, minutes before the close.
  public static func lastEntry(_ minutes: String) -> LocalizedStringResource {
    LocalizedStringResource("kit.simRules.lastEntry",
      defaultValue: "last entry \(minutes) min before close", bundle: bundle)
  }
  /// A check simulations skip: the trading gate.
  public static var gate: LocalizedStringResource {
    LocalizedStringResource("kit.simRules.check.gate", defaultValue: "the trading gate", bundle: bundle)
  }
  /// A check simulations skip: reconciliation with the broker.
  public static var reconciliation: LocalizedStringResource {
    LocalizedStringResource("kit.simRules.check.reconciliation", defaultValue: "broker reconciliation", bundle: bundle)
  }
  /// A check simulations skip: the live quote.
  public static var quote: LocalizedStringResource {
    LocalizedStringResource("kit.simRules.check.quote", defaultValue: "live quotes", bundle: bundle)
  }
  /// A check simulations skip: the broker request budget.
  public static var requestBudget: LocalizedStringResource {
    LocalizedStringResource("kit.simRules.check.requestBudget", defaultValue: "request limits", bundle: bundle)
  }
  /// A check simulations skip: buying power.
  public static var buyingPower: LocalizedStringResource {
    LocalizedStringResource("kit.simRules.check.buyingPower", defaultValue: "buying power", bundle: bundle)
  }
}
