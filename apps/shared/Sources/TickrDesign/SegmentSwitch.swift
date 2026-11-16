import SwiftUI

/// The apps' one segmented switch, as the iOS Activity screen draws it (Trades / Not taken /
/// Journal): equal segments on a card with a hairline, the chosen one raised on a sunken tile with
/// a hairline of its own. Used wherever one of a few choices switches what a screen shows (the
/// Activity panes, the Chart's range, the Lab's replay span, the Watching closely sort and filter),
/// so every switch in both apps looks alike.
///
/// A title that does not fit its segment wraps onto another line, never cut. Each segment answers
/// taps over the switch's full height (at least `Size.touchTarget` with its padding). The chosen
/// segment carries VoiceOver's selected trait; name the group with `.accessibilityLabel` on the
/// switch.
public struct SegmentSwitch<Value: Hashable>: View {
  let options: [(value: Value, title: Text)]
  @Binding var selection: Value
  /// Accessibility identifier of each segment (UI tests find them by it).
  let identifier: String?

  public init(options: [(value: Value, title: Text)], selection: Binding<Value>, identifier: String? = nil) {
    self.options = options
    _selection = selection
    self.identifier = identifier
  }

  public var body: some View {
    let shape = RoundedRectangle(cornerRadius: Tokens.Radius.large, style: .continuous)
    HStack(spacing: Tokens.Space.xs) {
      ForEach(Array(options.enumerated()), id: \.offset) { _, option in
        let chosen = option.value == selection
        Button {
          selection = option.value
        } label: {
          option.title
            .font(SegmentSwitchLook.font)
            .multilineTextAlignment(.center)
            .fixedSize(horizontal: false, vertical: true)
            .foregroundStyle(chosen ? Palette.textPrimary : Palette.textSecondary)
            .frame(maxWidth: .infinity, minHeight: Tokens.Size.iconButton)
            .padding(.horizontal, Tokens.Space.xs)
            .background {
              if chosen {
                let tile = RoundedRectangle(cornerRadius: Tokens.Radius.medium, style: .continuous)
                tile.fill(Palette.surfaceSunken)
                  .overlay { tile.strokeBorder(Palette.separator, lineWidth: Tokens.Stroke.thin) }
              }
            }
            // The tap area reaches over the switch's padding, so each segment is a full touch target.
            .padding(.vertical, Tokens.Space.xs)
            .contentShape(Rectangle())
            .padding(.vertical, -Tokens.Space.xs)
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(chosen ? [.isSelected] : [])
        .accessibilityIdentifier(identifier ?? "")
      }
    }
    .padding(Tokens.Space.xs)
    .background(Palette.surface, in: shape)
    .overlay(shape.strokeBorder(Palette.separator, lineWidth: Tokens.Stroke.thin))
    .animation(Tokens.Motion.quick, value: selection)
  }
}

/// `SegmentSwitch`'s look (a generic type cannot keep static constants of its own).
enum SegmentSwitchLook {
  /// The segments' titles: footnote semibold, as on the Activity board.
  static let font = Font.footnote.weight(.semibold)
  /// Symbol beside a segment the person cannot pick here (`ShownSegments`).
  static let lockSymbol = "lock.fill"
}

/// `SegmentSwitch`'s look for a choice that is made elsewhere and only shown here: the same card,
/// segments and raised tile on the shown value, but nothing to tap. A segment in `locked` carries a
/// lock and a dashed edge, as the iOS Settings board draws "Production · locked": the phone shows
/// the bot's environment, which only the Mac's config and the owner's authorization change.
/// `shown` nil raises no segment (a value outside the options). VoiceOver reads the group as one
/// element: name it with `.accessibilityLabel`, and its value is the shown segment.
public struct ShownSegments<Value: Hashable>: View {
  let options: [(value: Value, title: Text)]
  let shown: Value?
  let locked: Set<Value>

  public init(options: [(value: Value, title: Text)], shown: Value?, locked: Set<Value> = []) {
    self.options = options
    self.shown = shown
    self.locked = locked
  }

  public var body: some View {
    let shape = RoundedRectangle(cornerRadius: Tokens.Radius.large, style: .continuous)
    HStack(spacing: Tokens.Space.xs) {
      ForEach(Array(options.enumerated()), id: \.offset) { _, option in
        segment(option.title, chosen: option.value == shown, locked: locked.contains(option.value))
      }
    }
    .padding(Tokens.Space.xs)
    .background(Palette.surface, in: shape)
    .overlay(shape.strokeBorder(Palette.separator, lineWidth: Tokens.Stroke.thin))
    .accessibilityElement(children: .ignore)
    .accessibilityValue(options.first { $0.value == shown }?.title ?? Text(verbatim: ""))
  }

  private func segment(_ title: Text, chosen: Bool, locked: Bool) -> some View {
    let tile = RoundedRectangle(cornerRadius: Tokens.Radius.medium, style: .continuous)
    return HStack(spacing: Tokens.Space.xs) {
      if locked && !chosen {
        Image(systemName: SegmentSwitchLook.lockSymbol)
      }
      title
        .multilineTextAlignment(.center)
        .fixedSize(horizontal: false, vertical: true)
    }
    .font(SegmentSwitchLook.font)
    .foregroundStyle(chosen ? Palette.textPrimary : locked ? Palette.textQuaternary : Palette.textSecondary)
    .frame(maxWidth: .infinity, minHeight: Tokens.Size.iconButton)
    .padding(.horizontal, Tokens.Space.xs)
    .background {
      if chosen {
        tile.fill(Palette.surfaceSunken)
          .overlay { tile.strokeBorder(Palette.separator, lineWidth: Tokens.Stroke.thin) }
      } else if locked {
        tile.strokeBorder(Palette.separatorStrong,
          style: StrokeStyle(lineWidth: Tokens.Stroke.thin, dash: Tokens.Stroke.dashFine))
      }
    }
  }
}
