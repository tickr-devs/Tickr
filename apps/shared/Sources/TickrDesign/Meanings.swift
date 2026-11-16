import SwiftUI
import TickrKit

// The one look of each operator-facing meaning that both apps show: a stop's protection, a
// headline's severity, a strategy verdict, and the tones TickrKit gives events and exits
// (docs/design-system.md, "One meaning, one look"). Screens read these maps; they never pick a
// colour or a symbol for these meanings themselves (check_swift_rules.py, rules `result`,
// `stopsymbol`).

// MARK: - stop protection

extension StopState {
  /// A stop the broker confirmed working: a shield with a check (the Main board's badge).
  public static let verifiedSymbol = "checkmark.shield"
  /// A stop sent and not confirmed yet: an hourglass.
  public static let pendingSymbol = "hourglass"
  /// No stop at the broker: a crossed shield.
  public static let missingSymbol = "xmark.shield"
  /// The stop's state could not be read: an exclaimed shield.
  public static let unknownSymbol = "exclamationmark.shield"
  /// A stop's price level next to its figure (the Main board's "Stop" metric): a bare shield.
  public static let levelSymbol = "shield"

  /// The symbol of this state.
  public var symbol: String {
    switch self {
    case .verified: return Self.verifiedSymbol
    case .pending: return Self.pendingSymbol
    case .missing: return Self.missingSymbol
    case .unknown: return Self.unknownSymbol
    }
  }

  /// Profit only for a verified stop; loss for every other state, pending included: a stop the
  /// broker has not confirmed does not protect the position yet (the Main board draws "STOP
  /// PENDING" in red, with the alarm ring).
  public var tone: Tone { isProtected ? .profit : .loss }

  /// The tone of a set of positions' stops: profit when every one is verified, loss when any is
  /// not, neutral with no position.
  public static func combinedTone(_ states: [StopState]) -> Tone {
    guard !states.isEmpty else { return .neutral }
    return allProtected(states) ? .profit : .loss
  }
}

/// A position's protection as a glyph tile: the state's symbol in its tone, pulsing once each time
/// the state or the stop's price changes while the position is unprotected. Its hint and
/// VoiceOver label name the symbol and the state ("ABCD · pending"). The same in both apps;
/// `style` only sets its size (`.tile` beside a large symbol, `.compact` in rows).
public struct StopGlyph: View {
  let state: StopState
  let symbol: String
  let price: DecimalString?
  let style: StateGlyph.Style

  /// Between the symbol and the state in the label.
  static let separator = " · "

  public init(state: StopState, symbol: String, price: DecimalString? = nil, style: StateGlyph.Style = .compact) {
    self.state = state
    self.symbol = symbol
    self.price = price
    self.style = style
  }

  public var body: some View {
    StateGlyph(state.symbol, tone: state.tone, label: Text(verbatim: Self.label(state, symbol: symbol)),
      live: !state.isProtected, event: [AnyHashable(state), AnyHashable(price)], style: style)
  }

  /// "ABCD · verified": the glyph's label, in the person's language.
  public static func label(_ state: StopState, symbol: String) -> String {
    symbol + separator + Board.stopStateTitle(state)
  }
}

/// A position's protection as a small dot in its stop's tone, for a position's tab: present on
/// every tab, so a tab without one never reads as protected. It pulses once when an unprotected
/// stop changes. Decorative for VoiceOver: the tab says the state (`StopGlyph.label`).
public struct StopDot: View {
  let state: StopState

  public init(_ state: StopState) {
    self.state = state
  }

  public var body: some View {
    PulseDot(state.tone.fill, size: Tokens.Size.dotSmall, pulsing: !state.isProtected, event: state)
  }
}

// MARK: - headline severity

extension Severity {
  /// The tone of a headline with this severity: calm in profit, attention in warning, alarm in
  /// loss. `Headline.tone` refines the calm one.
  public var tone: Tone {
    switch self {
    case .ok: return .profit
    case .attention: return .warning
    case .alarm: return .loss
    }
  }
}

extension Headline {
  /// The headline's tone, as the boards draw it: a calm headline about held positions, every one
  /// protected, in the accent (Main "holding", MacMenuBar, IOSHome); a calm one with nothing held
  /// ("Entries allowed") in profit (Main "scanning"); attention in warning; alarm in loss.
  public var tone: Tone {
    severity == .ok && holding ? .accent : severity.tone
  }
}

// MARK: - verdicts

extension Verdict {
  /// The text colour of a verdict wherever it is written (the Chart's decisions, Watching
  /// closely), as the boards draw it: ENTER and HOLD in the accent (MacChart's explain card),
  /// WATCH in info, IGNORE in the series pink (Main, MacChart), EXIT in warning (no board draws
  /// an EXIT verdict; the Mac's existing colour). Always a text role.
  public var color: Color {
    switch self {
    case .enter, .hold: return Palette.accentText
    case .watch: return Palette.infoText
    case .ignore: return Palette.seriesATRText
    case .exit: return Palette.warningText
    }
  }

  /// The light and dark values of `color` (`PaletteContrastTests`).
  public var textSwatch: Swatch {
    switch self {
    case .enter, .hold: return .accentText
    case .watch: return .infoText
    case .ignore: return .seriesATRText
    case .exit: return .warningText
    }
  }
}

// MARK: - event and exit tones

extension Tone {
  /// The design tone of a TickrKit event tone (`Board.Event.Tone`, `Board.exitTone`): the one map
  /// from the bot's meanings to colours, so an exit forced at the session's end is amber on both
  /// apps and a protection failure red whatever the result.
  public init(_ tone: Board.Event.Tone) {
    switch tone {
    case .teal: self = .accent
    case .green: self = .profit
    case .blue: self = .info
    case .amber: self = .warning
    case .violet: self = .highlight
    case .red: self = .loss
    case .muted: self = .neutral
    }
  }

  /// The tone of a closed trade's exit reason: `Board.exitTone`.
  public static func exit(_ reason: String, result: Decimal) -> Tone {
    Tone(Board.exitTone(reason, result: result))
  }
}
