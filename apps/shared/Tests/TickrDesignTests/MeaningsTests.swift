import Foundation
import Testing
@testable import TickrDesign
import TickrKit

/// The one map of each operator-facing meaning (Meanings.swift): a stop's state, a headline's
/// severity, a verdict, an exit.
@Suite struct MeaningsTests {
  static let states: [StopState] = [.verified, .pending, .missing, .unknown]

  @Test("Only a verified stop protects; pending is red like missing (the Main board)")
  func stopTones() {
    #expect(Self.states.map(\.isProtected) == [true, false, false, false])
    #expect(Self.states.map(\.tone) == [.profit, .loss, .loss, .loss])
  }

  @Test("One symbol per stop state, none shared with the stop's level")
  func stopSymbols() {
    #expect(Self.states.map(\.symbol) == ["checkmark.shield", "hourglass", "xmark.shield", "exclamationmark.shield"])
    #expect(Set(Self.states.map(\.symbol)).count == Self.states.count)
    #expect(!Self.states.map(\.symbol).contains(StopState.levelSymbol))
  }

  @Test("A set of stops: neutral with none, profit only when every one is verified")
  func stopSets() {
    #expect(StopState.combinedTone([]) == .neutral)
    #expect(StopState.combinedTone([.verified, .verified]) == .profit)
    #expect(StopState.combinedTone([.verified, .pending]) == .loss)
    #expect(StopState.allProtected([]))
  }

  @Test("The glyph's label names the symbol and the state")
  func stopLabel() {
    #expect(StopGlyph.label(.pending, symbol: "ABCD") == "ABCD · " + Board.stopStateTitle(.pending))
  }

  @Test("Severity: calm profit, attention warning, alarm loss; a calm headline about held positions in the accent")
  func headlineTones() {
    #expect([Severity.ok, .attention, .alarm].map(\.tone) == [.profit, .warning, .loss])
    #expect(Headline(text: "", severity: .ok, holding: true).tone == .accent)
    #expect(Headline(text: "", severity: .ok).tone == .profit)
    #expect(Headline(text: "", severity: .alarm, holding: true).tone == .loss)
    #expect(Headline(text: "", severity: .attention).tone == .warning)
  }

  @Test("Verdicts: ENTER and HOLD accent, WATCH info, IGNORE pink, EXIT warning, all text roles")
  func verdicts() {
    let verdicts: [Verdict] = [.enter, .hold, .watch, .ignore, .exit]
    #expect(verdicts.map(\.textSwatch) == [.accentText, .accentText, .infoText, .seriesATRText, .warningText])
    #expect(verdicts.map(\.color)
      == [Palette.accentText, Palette.accentText, Palette.infoText, Palette.seriesATRText, Palette.warningText])
  }

  @Test("A tone's text colour and its swatch name the same role")
  func toneTexts() {
    let tones: [Tone] = [.neutral, .accent, .profit, .loss, .warning, .info, .highlight]
    #expect(tones.map(\.text) == [Palette.textTertiary, Palette.accentText, Palette.profitText, Palette.lossText,
      Palette.warningText, Palette.infoText, Palette.highlightText])
    #expect(tones.map(\.textSwatch) == [.textTertiary, .accentText, .profitText, .lossText, .warningText, .infoText,
      .highlightText])
    #expect(Palette.result(0) == Palette.textTertiary)
    #expect(Palette.result(d("-1")) == Palette.lossText)
  }

  @Test("Exits: forced amber and protection failures red whatever the result, the rest by the result")
  func exits() {
    #expect(Tone.exit("session-flatten", result: 5) == .warning)
    #expect(Tone.exit("stream-lost", result: -5) == .warning)
    #expect(Tone.exit("stop-rejected", result: 5) == .loss)
    #expect(Tone.exit("strategy-exit", result: 5) == .profit)
    #expect(Tone.exit("stopped-out", result: -5) == .loss)
    #expect(Tone.exit("strategy-exit", result: 0) == .neutral)
  }

  @Test("Every bot tone maps to one design tone")
  func eventTones() {
    let tones: [Board.Event.Tone] = [.teal, .green, .blue, .amber, .violet, .red, .muted]
    #expect(tones.map { Tone($0) } == [.accent, .profit, .info, .warning, .highlight, .loss, .neutral])
  }

  @Test("A money result: profit above zero, loss below, neutral at zero (never green)")
  func results() {
    #expect(Tone.result(d("0.01")) == .profit)
    #expect(Tone.result(d("-0.01")) == .loss)
    #expect(Tone.result(0) == .neutral)
  }
}
