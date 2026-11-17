import Foundation

/// An order's leg as the bot names it (`Leg` in src/tickr/engine/core.py).
public enum OrderLeg: String, Sendable {
  case entry, stop, exit
}

/// How the bot ended a run (`run-ended` causes in src/tickr/cli.py); any other reads as stopped.
enum BotStop: String {
  case finished, interrupted, refused
  case deadline = "shutdown-deadline"
}

extension Board {
  /// Event ids of the day's journal: `journal-<seq>`.
  static let journalID = "journal-"
  /// Between a started strategy's name and version in `subject` ("price-action@3").
  static let versionSeparator: Character = "@"

  /// The day's events (GET /events) as journal lines, newest first, in the person's language;
  /// an event of a kind this app does not know is left out, never shown as its code. `price`
  /// formats a price (TickrDesign `Money.price`).
  public static func journal(_ document: DayEventsV1, price: (Decimal) -> String) -> [Event] {
    document.events.reversed().compactMap { line($0, price: price) }
  }

  static func line(_ event: DayEventV1, price: (Decimal) -> String) -> Event? {
    guard let kind = event.event else { return nil }
    let symbol = event.symbol
    let quantity = event.quantity.map { CheckValue.text($0.value) }
    func at(_ value: DecimalString?) -> String? { value.map { price($0.value) } }
    let parts: (tag: LocalizedStringResource, text: String, tone: Event.Tone)
    switch kind {
    case .botStarted:
      parts = (L10n.journalBotOn, startedText(event.subject), .blue)
    case .botStopped:
      let stop = event.cause.flatMap(BotStop.init(rawValue:))
      let calm = stop != .deadline && stop != .interrupted
      parts = (L10n.journalBotOff, String(localized: stopTitle(stop)), calm ? .muted : .amber)
    case .entrySent:
      parts = (L10n.tagEnter, joined(quantityAt(symbol, quantity, at(event.price)),
        at(event.stopPrice).map { String(localized: L10n.journalStopAt($0)) }), .teal)
    case .filled:
      let leg = event.leg.flatMap(OrderLeg.init(rawValue:))
      parts = (filledTag(leg), quantityAt(symbol, quantity, at(event.price)),
        leg == .entry ? .teal : leg == .exit ? .blue : .muted)
    case .orderRefused:
      let leg = event.leg.flatMap(OrderLeg.init(rawValue:))
      parts = (L10n.tagRefused, joined(symbol, String(localized: refusedText(leg))), leg == .stop ? .red : .amber)
    case .stopPlaced:
      parts = (L10n.journalStopPlaced, quantityAt(symbol, quantity, at(event.stopPrice)), .green)
    case .stopMoved:
      parts = (L10n.journalStopMoved, joined(symbol, at(event.stopPrice).map { String(localized: L10n.journalTo($0)) }), .green)
    case .exitRequested:
      let cause = event.cause ?? ""
      parts = (L10n.journalExit, joined(symbol, exitReasonTitle(cause)), exitKind(cause) == .ordinary ? .blue : .amber)
    case .tradeClosed:
      let cause = event.cause ?? ""
      parts = (L10n.journalClosed, joined(symbol, exitReasonTitle(cause)), closedTone(cause))
    case .fenceSet, .fenceCleared:
      let title = String(localized: event.subject.flatMap(ApiText.fenceKindCase)?.title ?? L10n.fenceOther)
      parts = (kind == .fenceSet ? L10n.tagFence : L10n.journalFenceCleared, joined(symbol, title),
        kind == .fenceSet ? .amber : .green)
    case .commandDone, .commandRefused:
      let title = event.subject.flatMap(CommandKind.init(rawValue:)).map(commandTitle)
        ?? String(localized: L10n.journalCommandOther)
      let done = kind == .commandDone
      parts = (done ? L10n.tagDone : L10n.tagRefused,
        done ? title : joined(title, event.cause), done ? .blue : .red)
    }
    return Event(id: "\(journalID)\(event.seq)", time: event.at.date, tag: String(localized: parts.tag),
      text: parts.text, tone: parts.tone)
  }

  /// "ABCD 100 @ 4.12" (the cards' own wording) when all three are known, else the known ones.
  static func quantityAt(_ symbol: String?, _ quantity: String?, _ price: String?) -> String {
    guard let symbol, let quantity, let price else {
      return joined(symbol, quantity, price.map { String(localized: L10n.journalAt($0)) })
    }
    return String(localized: L10n.eventQuantityAt(symbol, quantity, price))
  }

  /// The present parts of a line, joined with " · ".
  static func joined(_ parts: String?...) -> String {
    parts.compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: journalSeparator)
  }

  /// Between the parts of a journal line.
  static let journalSeparator = " · "

  static func startedText(_ subject: String?) -> String {
    guard let subject, let cut = subject.lastIndex(of: versionSeparator) else {
      return String(localized: L10n.journalStarted)
    }
    let name = String(subject[..<cut])
    let version = String(subject[subject.index(after: cut)...])
    return String(localized: L10n.journalStartedWith(name, version))
  }

  static func stopTitle(_ stop: BotStop?) -> LocalizedStringResource {
    switch stop {
    case .interrupted: L10n.journalInterrupted
    case .refused: L10n.journalRefusedStart
    case .deadline: L10n.journalDeadline
    case .finished, nil: L10n.journalStopped
    }
  }

  static func filledTag(_ leg: OrderLeg?) -> LocalizedStringResource {
    switch leg {
    case .entry: L10n.journalBought
    case .stop: L10n.journalStopFilled
    case .exit, nil: L10n.journalSold
    }
  }

  static func refusedText(_ leg: OrderLeg?) -> LocalizedStringResource {
    switch leg {
    case .entry: L10n.journalEntryRefused
    case .stop: L10n.journalStopRefused
    case .exit, nil: L10n.journalExitRefused
    }
  }

  /// A closed trade's tone without its result: red only when protection failed, amber for a
  /// forced close, otherwise quiet (a stop hit is an ordinary close, not a danger).
  static func closedTone(_ cause: String) -> Event.Tone {
    switch exitKind(cause) {
    case .protectionFailure: .red
    case .forced: .amber
    case .ordinary: .muted
    }
  }
}
