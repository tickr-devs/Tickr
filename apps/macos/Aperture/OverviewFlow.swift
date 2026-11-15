import ApertureDesign
import ApertureKit
import SwiftUI

// The Overview's parts read from GET /flow and GET /watching: the screener chips under the
// pipeline, and the symbols the strategy is watching closely.

/// Values of the pipeline stages that combine the live status with the day's flow.
enum FlowStages {
  /// The reconcile stage: its value, or nil with why (a missing value never reads as fine).
  struct Reconcile: Equatable {
    let value: String?
    let note: String?
  }

  /// Live fences come first (they come from the status, newer than any reconciliation), then the
  /// latest reconciliation's mismatch or match; without one, unknown with the bot's cause.
  static func reconcile(liveFences: [Fence]?, latest: FlowReconcileV1?) -> Reconcile {
    if let fences = liveFences, !fences.isEmpty {
      return Reconcile(value: String(localized: L10n.Common.fences(fences.count)), note: nil)
    }
    switch latest?.agreement {
    case .mismatch: return Reconcile(value: String(localized: L10n.Screener.mismatch), note: nil)
    case .match: return Reconcile(value: String(localized: L10n.Flow.match), note: nil)
    case nil: return Reconcile(value: nil, note: latest?.agreementCause)
    }
  }
}

/// The day's screener symbols as chips scrolling across the top of the live flow, as in the
/// design: the symbol, then its screener and change from the previous close in the screener's
/// colour. A symbol the universe left out is dimmed and struck through, the cause in its tooltip.
/// Without chips the event ticker takes the place, with a note why.
struct ScreenerChips: View {
  @Environment(AppModel.self) private var model

  /// Accessibility id of the chip row, for the UI tests.
  static let id = "overview-screener-chips"

  var body: some View {
    Group {
      if let chips = model.flow?.chips, !chips.isEmpty {
        Marquee(moving: model.isLive) {
          HStack(spacing: Tokens.Space.s) {
            ForEach(chips, id: \.symbol) { chip in
              Chip(chip: chip)
            }
          }
          .padding(.trailing, Tokens.Space.s)
        }
      } else {
        VStack(alignment: .leading, spacing: Tokens.Space.xs) {
          Ticker(model.tickerItems, moving: model.isLive)
            .frame(height: Tokens.Size.ticker)
          if model.flow != nil {
            Footnote(L10n.Screener.none)
          } else if let error = model.flowError {
            Footnote(L10n.Screener.unavailable(error), color: Palette.warningText)
          }
        }
      }
    }
    .accessibilityElement(children: .contain)
    .accessibilityIdentifier(Self.id)
  }
}

private struct Chip: View {
  let chip: FlowChipV1

  /// Decimals of the change ("+18.2%").
  static let changeDigits = 1

  var body: some View {
    let excluded = chip.excludedCause != nil
    VStack(alignment: .leading, spacing: Tokens.Space.xxs) {
      Text(verbatim: chip.symbol)
        .font(Tokens.Typography.chip)
        .strikethrough(excluded)
        .foregroundStyle(excluded ? Palette.textQuaternary : Palette.textPrimary)
      Text(detail)
        .font(Tokens.Typography.annotation)
        .foregroundStyle(excluded ? Palette.textQuaternary : color)
    }
    .lineLimit(1)
    .fixedSize()
    .padding(.horizontal, Tokens.Space.m)
    .padding(.vertical, Tokens.Space.s)
    .frame(minWidth: Tokens.Size.flowChip, alignment: .leading)
    .background(Palette.surfaceSunken, in: RoundedRectangle(cornerRadius: Tokens.Radius.medium))
    .overlay {
      RoundedRectangle(cornerRadius: Tokens.Radius.medium).strokeBorder(Palette.separator, lineWidth: Tokens.Stroke.thin)
    }
    .help(Text(verbatim: help))
    .accessibilityElement(children: .combine)
  }

  /// The first screener that listed the symbol names its colour and its word.
  private var source: ScreenerSource? { chip.sources.first }

  /// "gainers +18.2%"; "skipped" for a symbol the universe left out.
  private var detail: LocalizedStringResource {
    if chip.excludedCause != nil { return L10n.Flow.chipSkipped }
    let word = switch source {
    case .gainers: L10n.Flow.chipGainers
    case .losers: L10n.Flow.chipLosers
    case .topActives, nil: L10n.Flow.chipActives
    }
    let change = chip.changePct.map {
      String(localized: L10n.Trades.percent(Money.signed($0.value, fractionDigits: Self.changeDigits)))
    }
    return L10n.Flow.chip(String(localized: word), change ?? Format.missing)
  }

  /// Gainers green, top actives blue, losers pink, as in the design.
  private var color: Color {
    switch source {
    case .gainers: Palette.profitText
    case .losers: Palette.seriesATRText
    case .topActives, nil: Palette.infoText
    }
  }

  /// Why the chip looks as it does: the exclusion, the missing change, then the screeners.
  private var help: String {
    let sources = chip.sources.map { String(localized: $0.title) }.joined(separator: Format.listSeparator)
    return [chip.excludedCause.map { String(localized: L10n.Screener.excluded($0)) }, chip.changeCause, sources]
      .compactMap { $0 }
      .joined(separator: Format.lineSeparator)
  }
}

extension ScreenerSource {
  var title: LocalizedStringResource {
    switch self {
    case .gainers: L10n.Screener.gainers
    case .losers: L10n.Screener.losers
    case .topActives: L10n.Screener.topActives
    }
  }
}

/// The symbols the strategy is closest to entering, with the check that decides each and how
/// close it is to passing.
struct WatchingCard: View {
  @Environment(AppModel.self) private var model

  /// Display cap on the rows shown (not an API limit); the card says how many there are in all.
  static let shown = 6
  /// Width of a closeness bar, in points.
  static let barWidth: CGFloat = 120
  /// Accessibility id of the card, for the UI tests.
  static let id = "overview-watching"

  var body: some View {
    Card(L10n.Watching.title) {
      if let watching = model.watching, watching.total > 0 {
        Text(L10n.Watching.count(min(watching.rows.count, Self.shown), watching.total))
          .font(Tokens.Typography.captionMono)
          .foregroundStyle(Palette.textQuaternary)
      }
    } content: {
      if let watching = model.watching {
        if watching.rows.isEmpty {
          Footnote(L10n.Watching.none)
        } else {
          Grid(alignment: .leading, horizontalSpacing: Tokens.Space.l, verticalSpacing: Tokens.Space.s) {
            ForEach(watching.rows.prefix(Self.shown), id: \.symbol) { row in
              WatchingRow(row: row)
            }
          }
        }
      } else if let error = model.watchingError {
        Footnote(L10n.Watching.unavailable(error), color: Palette.warningText)
      } else {
        ProgressView().controlSize(.small)
      }
    }
    .accessibilityElement(children: .contain)
    .accessibilityIdentifier(Self.id)
  }
}

private struct WatchingRow: View {
  let row: WatchingRowV1

  var body: some View {
    GridRow {
      Text(verbatim: row.symbol).font(Tokens.Typography.calloutMono.weight(.semibold))
      Badge(row.verdict.title, color: row.verdict.color)
      Group {
        if let check = row.decisiveCheck {
          Text(verbatim: check.comparisonText)
            .foregroundStyle(Palette.textSecondary)
            .help(Text(verbatim: check.note))
        } else {
          Text(L10n.Watching.noCheck).foregroundStyle(Palette.textQuaternary)
        }
      }
      .font(Tokens.Typography.captionMono)
      .lineLimit(1)
      closeness
      Text(verbatim: Format.marketMinute(row.at.date))
        .font(Tokens.Typography.captionMono)
        .foregroundStyle(Palette.textQuaternary)
    }
  }

  @ViewBuilder private var closeness: some View {
    if let value = row.closeness?.value {
      ProgressView(value: NSDecimalNumber(decimal: value).doubleValue)
        .tint(Palette.accent)
        .frame(width: WatchingCard.barWidth)
        .accessibilityLabel(Text(L10n.Watching.closeness))
        .help(Text(L10n.Watching.closeness))
    } else {
      Text(verbatim: Format.missing)
        .foregroundStyle(Palette.textQuaternary)
        .frame(width: WatchingCard.barWidth, alignment: .leading)
        .help(row.closenessCause.map { Text(verbatim: $0) } ?? Text(L10n.Watching.closeness))
    }
  }
}

extension CheckV1 {
  /// "rule measured operator threshold" as the bot recorded them; a missing side is a dash.
  var comparisonText: String {
    "\(rule) \(measured?.text ?? Format.missing) \(`operator`) \(threshold?.text ?? Format.missing)"
  }
}
