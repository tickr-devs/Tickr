import Foundation

/// The Stop bot question on both apps (mockup Main and IOSHome, backlog V4), in English with their
/// Spanish translation in Resources/Localizable.xcstrings. With positions open it offers Close and
/// stop or Leave open; with none it only confirms.
public enum StopText {
  /// The question with positions open; argument: how many.
  public static func askTitle(_ count: Int) -> LocalizedStringResource {
    LocalizedStringResource("kit.stop.askTitle",
      defaultValue: "Stop the bot with \(count) positions open?", bundle: L10n.bundle)
  }
  /// Under the question; argument: the open symbols as a list ("ABCD and KLMN").
  public static func askBody(_ symbols: String) -> LocalizedStringResource {
    LocalizedStringResource("kit.stop.askBody", defaultValue: "Choose what happens to \(symbols).", bundle: L10n.bundle)
  }
  public static var closeAndStop: LocalizedStringResource {
    LocalizedStringResource("kit.stop.closeAndStop", defaultValue: "Close and stop", bundle: L10n.bundle)
  }
  /// What Close and stop does; argument: the open symbols as a list.
  public static func closeAndStopDetail(_ symbols: String) -> LocalizedStringResource {
    LocalizedStringResource("kit.stop.closeAndStopDetail",
      defaultValue: "Closes \(symbols) at market, then stops the bot.", bundle: L10n.bundle)
  }
  public static var leaveOpen: LocalizedStringResource {
    LocalizedStringResource("kit.stop.leaveOpen", defaultValue: "Leave open", bundle: L10n.bundle)
  }
  public static var leaveOpenDetail: LocalizedStringResource {
    LocalizedStringResource("kit.stop.leaveOpenDetail",
      // swiftlint:disable:next line_length
      defaultValue: "The bot stops at once and nothing closes them. Positions stay open. They close only at the broker, by their stop or your own orders. Broker stops are day orders and expire at 4:00 PM ET, so a position can stay overnight without a stop.",
      bundle: L10n.bundle)
  }
  /// The question's message with nothing open.
  public static var flatDetail: LocalizedStringResource {
    LocalizedStringResource("kit.stop.flatDetail",
      // swiftlint:disable:next line_length
      defaultValue: "No positions are open. New entries are blocked, working entry orders are cancelled, then the bot stops. It stays stopped, also tomorrow, until you press Start bot in Settings.",
      bundle: L10n.bundle)
  }

  /// One choice in a message that lists both (the Mac's alert): its name, then what it does.
  public static func choice(_ head: String, _ detail: String) -> LocalizedStringResource {
    LocalizedStringResource("kit.stop.choice", defaultValue: "\(head): \(detail)", bundle: L10n.bundle)
  }

  /// Between the paragraphs of one message.
  static let paragraph = "\n\n"

  /// The whole message of the question in one text, for an alert: with nothing open what the stop
  /// does; with positions open the two choices, each with what it does.
  public static func message(symbols: [String]) -> String {
    guard !symbols.isEmpty else { return String(localized: flatDetail) }
    let list = Self.symbols(symbols)
    return [
      String(localized: askBody(list)),
      String(localized: choice(String(localized: closeAndStop), String(localized: closeAndStopDetail(list)))),
      String(localized: choice(String(localized: leaveOpen), String(localized: leaveOpenDetail))),
    ].joined(separator: paragraph)
  }

  /// The open symbols as the question says them: "ABCD and KLMN", in the reader's language.
  public static func symbols(_ symbols: [String]) -> String {
    symbols.formatted(.list(type: .and))
  }
}
