import Foundation
import Testing
@testable import TickrDesign
import TickrKit

/// The label under a simulated result (backlog T12) names the rules' values in words and leaves
/// out what it does not know, never showing a bot.toml key or a check's code.
@Suite struct SimulationRulesNoteTests {
  private static let values = [
    "risk.planned_loss_usd": "50",
    "risk.position_notional_cap_usd": "1000",
    "risk.max_daily_loss_usd": "5000",
    "risk.max_open_positions": "6",
    "risk.symbol_cooldown_minutes_after_loss": "30",
    "session.last_entry_minutes_before_close": "25",
    "risk.min_daily_room_usd": "10",
  ]

  private func rules(values: [String: String] = values, skipped: [String]) -> SimulationRulesV1 {
    SimulationRulesV1(applied: ["session", "own_state", "decision", "money"], notSimulated: skipped, values: values)
  }

  @Test func namesTheKnownValuesInOrderAndNoKey() {
    let parts = SimulationRulesWords.values(Self.values)
    #expect(parts.count == 6)
    #expect(parts.first?.contains("$50.00") == true)
    #expect(parts.contains { $0.contains("$1,000.00") })
    #expect(parts.contains { $0.contains("30") })
    let sentence = SimulationRulesWords.rules(rules(skipped: []))
    #expect(!sentence.contains("risk.") && !sentence.contains("session."))
  }

  @Test func aPauseTurnedOffSaysSo() {
    var values = Self.values
    values["risk.symbol_cooldown_minutes_after_loss"] = "off"
    let parts = SimulationRulesWords.values(values)
    #expect(parts.contains(String(localized: SimulationRulesText.noPauseAfterLoss)))
  }

  @Test func noKnownValueStillSaysTheRulesApplied() {
    #expect(SimulationRulesWords.rules(rules(values: ["risk.something_new": "1"], skipped: []))
      == String(localized: SimulationRulesText.underRulesPlain))
  }

  @Test func checksNotSimulatedInWordsUnknownOnesLeftOut() throws {
    let text = try #require(SimulationRulesWords.notSimulated(rules(skipped: ["gate", "buying_power", "new_check"])))
    #expect(text.contains(String(localized: SimulationRulesText.gate)))
    #expect(text.contains(String(localized: SimulationRulesText.buyingPower)))
    #expect(!text.contains("new_check") && !text.contains("buying_power"))
    #expect(SimulationRulesWords.notSimulated(rules(skipped: ["new_check"])) == nil)
  }
}
