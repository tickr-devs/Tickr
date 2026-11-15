import ApertureDesign
import ApertureKit
import SwiftUI

/// A recorded day's clean-day verdict as a small mark: a seal for clean, a warning for not clean,
/// nothing while unknown (the tooltip then says why). The verdict is the bot's, from its journal.
struct CleanMark: View {
  let clean: CleanDay


  var body: some View {
    Group {
      switch clean.verdict {
      case .clean:
        Image(systemName: "checkmark.seal.fill").foregroundStyle(Palette.profitText)
      case .notClean:
        Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(Palette.lossText)
      case .unknown:
        EmptyView()
      }
    }
    .font(Tokens.Typography.caption)
    .help(Text(verbatim: Self.text(clean)))
    .accessibilityLabel(Text(verbatim: Self.text(clean)))
  }

  /// The verdict in words: what made a day not clean, or why the verdict is unknown.
  static func text(_ clean: CleanDay) -> String {
    switch clean.verdict {
    case .clean:
      return String(localized: L10n.Trades.cleanDay)
    case .notClean:
      return String(localized: L10n.Trades.notCleanDay(clean.causes.map(\.text).joined(separator: Format.listSeparator)))
    case .unknown:
      return String(localized: L10n.Trades.cleanUnknown(clean.cause ?? Format.missing))
    }
  }
}
