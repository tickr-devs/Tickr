import Foundation
import SwiftUI
import TickrKit

// The label under a simulated result on both apps (backlog T12, docs/api.md "Simulation rules"):
// which of the live bot's rules it was computed under, with the values the owner knows, and what
// a simulation cannot check. Values come from the document's `values` by their bot.toml keys;
// a key this app does not know is left out, never shown as a code.

/// The words of a `SimulationRulesV1`: one sentence for the rules and values, one for what was not
/// simulated (nil when nothing is named).
public enum SimulationRulesWords {
  /// bot.toml keys of the values the label names, in its order.
  static let plannedLossKey = "risk.planned_loss_usd"
  static let positionCapKey = "risk.position_notional_cap_usd"
  static let dailyLimitKey = "risk.max_daily_loss_usd"
  static let openPositionsKey = "risk.max_open_positions"
  static let pauseKey = "risk.symbol_cooldown_minutes_after_loss"
  static let lastEntryKey = "session.last_entry_minutes_before_close"
  /// The pause's value when it is turned off.
  static let pauseOff = "off"
  /// Codes of the checks a simulation skips (`not_simulated`).
  static let gate = "gate"
  static let reconciliation = "reconciliation"
  static let quote = "quote"
  static let requestBudget = "request_budget"
  static let buyingPower = "buying_power"

  /// "Computed under the bot's rules: planned loss $50.00, position cap $1,000.00, …".
  public static func rules(_ rules: SimulationRulesV1) -> String {
    let parts = values(rules.values)
    return parts.isEmpty
      ? String(localized: SimulationRulesText.underRulesPlain)
      : String(localized: SimulationRulesText.underRules(parts.formatted(.list(type: .and))))
  }

  /// "Not simulated: the trading gate, live quotes and buying power."; nil when none is known.
  public static func notSimulated(_ rules: SimulationRulesV1) -> String? {
    let checks = rules.notSimulated.compactMap(check).map { String(localized: $0) }
    return checks.isEmpty ? nil : String(localized: SimulationRulesText.notSimulated(checks.formatted(.list(type: .and))))
  }

  static func values(_ values: [String: String]) -> [String] {
    var parts: [LocalizedStringResource] = []
    if let usd = amount(values[plannedLossKey]) { parts.append(SimulationRulesText.plannedLoss(Money.usd(usd))) }
    if let usd = amount(values[positionCapKey]) { parts.append(SimulationRulesText.positionCap(Money.usd(usd))) }
    if let usd = amount(values[dailyLimitKey]) { parts.append(SimulationRulesText.dailyLimit(Money.usd(usd))) }
    if let count = whole(values[openPositionsKey]) { parts.append(SimulationRulesText.openPositions(count)) }
    if values[pauseKey] == pauseOff {
      parts.append(SimulationRulesText.noPauseAfterLoss)
    } else if let minutes = whole(values[pauseKey]) {
      parts.append(SimulationRulesText.pauseAfterLoss(minutes))
    }
    if let minutes = whole(values[lastEntryKey]) { parts.append(SimulationRulesText.lastEntry(minutes)) }
    return parts.map { String(localized: $0) }
  }

  static func check(_ code: String) -> LocalizedStringResource? {
    switch code {
    case gate: SimulationRulesText.gate
    case reconciliation: SimulationRulesText.reconciliation
    case quote: SimulationRulesText.quote
    case requestBudget: SimulationRulesText.requestBudget
    case buyingPower: SimulationRulesText.buyingPower
    default: nil
    }
  }

  /// A dollar value as the bot wrote it ("50", "1000.5"); nil when it is not a number.
  static func amount(_ text: String?) -> Decimal? {
    text.flatMap { Decimal(string: $0, locale: Locale(identifier: Money.posixLocale)) }
  }

  /// A whole count as the bot wrote it ("6"); nil when it is not one.
  static func whole(_ text: String?) -> String? {
    text.flatMap(Int.init).map(String.init)
  }
}

/// The label itself: a small note under a simulated result, the same on both apps.
public struct SimulationRulesNote: View {
  /// Symbol of the note.
  static let symbol = "checkmark.shield"

  let rules: SimulationRulesV1

  public init(_ rules: SimulationRulesV1) { self.rules = rules }

  public var body: some View {
    let text = [SimulationRulesWords.rules(rules), SimulationRulesWords.notSimulated(rules)]
      .compactMap { $0 }.joined(separator: SimulationRulesNote.separator)
    InfoCaption(verbatim: text, systemImage: Self.symbol)
  }

  /// Between the two sentences.
  static let separator = " "
}
