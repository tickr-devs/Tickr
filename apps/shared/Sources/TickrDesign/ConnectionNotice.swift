import SwiftUI

/// Fixed parts of `ConnectionNotice` (a generic type holds no stored constants).
public enum ConnectionNoticeLook {
  /// Symbol of a link that is down.
  public static let offlineSymbol = "wifi.exclamationmark"
  /// Symbol of a link not set up yet.
  public static let setupSymbol = "link"
  /// Symbol of the details' disclosure, and its turn when open, in degrees.
  static let chevronSymbol = "chevron.right"
  static let openDegrees: Double = 90
}

/// The apps' one way of saying the link to the bot is down or not set up (TickrKit
/// `ConnectionState`): a short title and one plain sentence in the tone of the state, the actions
/// (retry, connection settings), and the technical detail (address, port, the API's own cause)
/// only behind "Details". It never replaces a screen: `banner` stands above the screen's last
/// known data, `empty` fills the card area when there is no data yet.
public struct ConnectionNotice<Actions: View>: View {
  public enum Style: Sendable { case banner, empty }

  let style: Style
  let title: Text
  let message: Text
  let tone: Tone
  let symbol: String
  let detailsLabel: Text
  let details: [String]
  let actions: Actions
  @State private var showsDetails = false
  @Environment(\.dynamicTypeSize) private var typeSize

  /// `details` are the technical lines shown under "Details"; none hides the disclosure.
  public init(
    _ style: Style, title: LocalizedStringResource, message: LocalizedStringResource, tone: Tone,
    systemImage: String, detailsLabel: LocalizedStringResource, details: [String],
    @ViewBuilder actions: () -> Actions
  ) {
    self.style = style
    self.title = Text(title)
    self.message = Text(message)
    self.tone = tone
    self.symbol = systemImage
    self.detailsLabel = Text(detailsLabel)
    self.details = details
    self.actions = actions()
  }

  public var body: some View {
    switch style {
    case .banner: banner
    case .empty: empty
    }
  }

  private var banner: some View {
    let shape = RoundedRectangle(cornerRadius: Tokens.Radius.medium, style: .continuous)
    return HStack(alignment: .top, spacing: Tokens.Space.m) {
      Image(systemName: symbol)
        .font(Tokens.Typography.symbol)
        .foregroundStyle(tone.fill)
        .accessibilityHidden(true)
      VStack(alignment: .leading, spacing: Tokens.Space.s) {
        VStack(alignment: .leading, spacing: Tokens.Space.xxs) {
          title
            .font(Tokens.Typography.callout.weight(.semibold))
            .foregroundStyle(tone.text)
          message
            .font(Tokens.Typography.caption)
            .foregroundStyle(Palette.textSecondary)
        }
        .fixedSize(horizontal: false, vertical: true)
        .accessibilityElement(children: .combine)
        controls
      }
      Spacer(minLength: 0)
    }
    .padding(Tokens.Space.m)
    .background(tone.fill.opacity(Tokens.Opacity.wash), in: shape)
    .overlay(shape.strokeBorder(tone.fill.opacity(Tokens.Opacity.border), lineWidth: Tokens.Stroke.thin))
  }

  private var empty: some View {
    VStack(spacing: Tokens.Space.m) {
      Image(systemName: symbol)
        .font(Tokens.Typography.heading)
        .foregroundStyle(tone.fill)
        .frame(width: Tokens.Size.iconWell, height: Tokens.Size.iconWell)
        .background(tone.fill.opacity(Tokens.Opacity.wash), in: Circle())
        .accessibilityHidden(true)
      VStack(spacing: Tokens.Space.xs) {
        title
          .font(Tokens.Typography.body.weight(.semibold))
          .foregroundStyle(Palette.textPrimary)
        message
          .font(Tokens.Typography.callout)
          .foregroundStyle(Palette.textSecondary)
      }
      .multilineTextAlignment(.center)
      .fixedSize(horizontal: false, vertical: true)
      .accessibilityElement(children: .combine)
      controls
    }
    .frame(maxWidth: .infinity)
    .padding(.horizontal, Tokens.Space.l)
    .padding(.vertical, Tokens.Space.xl)
    .tickrCard()
  }

  /// The actions in a row (in a column at accessibility text sizes), then "Details" and, opened,
  /// the technical lines in mono, selectable.
  private var controls: some View {
    VStack(alignment: style == .banner ? .leading : .center, spacing: Tokens.Space.s) {
      if typeSize.isAccessibilitySize {
        VStack(alignment: .leading, spacing: Tokens.Space.s) { actions }
      } else {
        HStack(spacing: Tokens.Space.s) { actions }
      }
      if !details.isEmpty {
        Button {
          withAnimation(Tokens.Motion.quick) { showsDetails.toggle() }
        } label: {
          HStack(spacing: Tokens.Space.xs) {
            Image(systemName: ConnectionNoticeLook.chevronSymbol)
              .font(Tokens.Typography.chevron)
              .rotationEffect(showsDetails ? .degrees(ConnectionNoticeLook.openDegrees) : .zero)
            detailsLabel
          }
          .font(Tokens.Typography.caption.weight(.semibold))
          .foregroundStyle(Palette.textTertiary)
          .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(showsDetails ? [.isSelected] : [])
        if showsDetails {
          VStack(alignment: .leading, spacing: Tokens.Space.xxs) {
            ForEach(details, id: \.self) { line in
              Text(verbatim: line)
            }
          }
          .font(Tokens.Typography.captionMono)
          .foregroundStyle(Palette.textTertiary)
          .multilineTextAlignment(.leading)
          .fixedSize(horizontal: false, vertical: true)
          .frame(maxWidth: .infinity, alignment: .leading)
          .textSelection(.enabled)
          .transition(.opacity)
        }
      }
    }
  }
}
