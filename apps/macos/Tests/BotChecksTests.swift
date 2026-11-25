import Foundation
import Testing
import TickrDesign
@testable import TickrMac

@Suite("The Bot card's status line over its safety checks")
@MainActor
struct BotChecksTests {
  private func check(_ id: BotCheck.Kind, _ tone: Tone) -> BotCheck {
    BotCheck(id: id, label: L10n.Safety.brokerStops, value: String(), tone: tone)
  }

  @Test("Every check passing (calm neutral ones too) counts them all and leads with none")
  func allPass() {
    let summary = BotChecks.summary([check(.reconcile, .profit), check(.stops, .neutral), check(.stream, .neutral),
      check(.read, .profit)])
    #expect(summary == BotChecks.Summary(passed: 4, total: 4, worst: nil))
  }

  @Test("A loss leads before an earlier warning; both count as failing")
  func lossLeads() {
    let summary = BotChecks.summary([check(.reconcile, .warning), check(.stops, .loss), check(.read, .profit)])
    #expect(summary == BotChecks.Summary(passed: 1, total: 3, worst: .stops))
  }

  @Test("Without a loss the first warning leads")
  func firstWarning() {
    let summary = BotChecks.summary([check(.reconcile, .profit), check(.stream, .warning), check(.read, .warning)])
    #expect(summary.worst == .stream)
    #expect(summary.passed == 1)
  }

  @Test("A silent bot's age reads in seconds, then in minutes, never below zero")
  func silence() {
    let written = Date(timeIntervalSince1970: 0)
    #expect(BotChecks.silence(since: written, now: written.addingTimeInterval(47))
      == String(localized: L10n.Toolbar.ageSeconds(47)))
    #expect(BotChecks.silence(since: written, now: written.addingTimeInterval(TimeInterval(Format.secondsShownUpTo)))
      == String(localized: L10n.Toolbar.ageMinutes(Format.secondsShownUpTo / Format.secondsPerMinute)))
    #expect(BotChecks.silence(since: written, now: written.addingTimeInterval(-5))
      == String(localized: L10n.Toolbar.ageSeconds(0)))
  }
}
