import SwiftUI
import TickrKit

// MARK: - scroll indicators

public extension View {
  /// Hides the scroll indicators of every scroll view, list and form below this view (owner,
  /// 2026-11-18: no scroll bars anywhere, on the Mac or the phone). `.never` also overrides the
  /// Mac's "Always show scroll bars" setting. Each scene's root applies it once; sheets and
  /// popovers inherit it. The `scrollbars` rule of `check_swift_rules.py` keeps the apps from
  /// setting indicators view by view, so this stays the one place.
  func withoutScrollIndicators() -> some View {
    scrollIndicators(.never, axes: [.horizontal, .vertical])
  }

  /// What stands in for the hidden indicators on a vertical scroll view, list or form (owner's
  /// pick, variant B of the 2026-11-18 mockups): an edge with more content beyond it fades the
  /// content out, and while more is below, a small capsule with a down chevron sits on the bottom
  /// edge; a click or tap on it scrolls a page. At the top and the end the hints go away.
  /// Apply it on the scroll view itself (it reads that view's geometry and position).
  func scrollEdgeHints() -> some View {
    modifier(ScrollEdgeHints(external: nil))
  }

  /// `scrollEdgeHints()` for a scroll view whose position the screen also drives (a pane that
  /// goes back to the top when another tab is picked): the hints scroll through the same binding.
  func scrollEdgeHints(position: Binding<ScrollPosition>) -> some View {
    modifier(ScrollEdgeHints(external: position))
  }
}

/// Which edges of a scroll view have more content beyond them, and the insets (a bar drawn over
/// the content, as the phone's tab bar) the hints keep clear of.
struct ScrollEdges: Equatable {
  var above = false
  var below = false
  var insetTop: CGFloat = 0
  var insetBottom: CGFloat = 0

  init() {}

  init(_ geometry: ScrollGeometry) {
    let tolerance = Tokens.Scroll.edgeTolerance
    insetTop = geometry.contentInsets.top
    insetBottom = geometry.contentInsets.bottom
    above = geometry.contentOffset.y + insetTop > tolerance
    below = geometry.contentSize.height + insetBottom - geometry.contentOffset.y
      - geometry.containerSize.height > tolerance
  }
}

/// Where the next page starts: kept out of view state, so following the scroll every frame
/// redraws nothing.
@MainActor final class ScrollPage {
  var offset: CGFloat = 0
  var visible: CGFloat = 0
  var end: CGFloat = 0

  func follow(_ geometry: ScrollGeometry) {
    offset = geometry.contentOffset.y
    visible = geometry.containerSize.height - geometry.contentInsets.top - geometry.contentInsets.bottom
    end = geometry.contentSize.height + geometry.contentInsets.bottom - geometry.containerSize.height
  }

  /// One page further down, never past the end.
  var next: CGFloat { min(offset + visible * Tokens.Scroll.pageShare, end) }
}

struct ScrollEdgeHints: ViewModifier {
  let external: Binding<ScrollPosition>?
  @State private var own = ScrollPosition(edge: .top)
  @State private var edges = ScrollEdges()
  @State private var page = ScrollPage()
  @Environment(\.accessibilityReduceMotion) private var reduceMotion

  private var position: Binding<ScrollPosition> { external ?? $own }
  private var motion: Animation? { reduceMotion ? nil : .easeOut(duration: Tokens.Scroll.fadeSeconds) }

  func body(content: Content) -> some View {
    content
      .scrollPosition(position)
      .onScrollGeometryChange(for: ScrollEdges.self) { ScrollEdges($0) } action: { _, new in
        edges = new
      }
      .onScrollGeometryChange(for: ScrollGeometry.self) { $0 } action: { _, new in
        page.follow(new)
      }
      .mask { fadeMask.animation(motion, value: edges) }
      .overlay(alignment: .bottom) {
        if edges.below {
          chevron
            .padding(.bottom, edges.insetBottom + Tokens.Space.s)
            .transition(.opacity)
        }
      }
      .animation(motion, value: edges.below)
  }

  /// Opaque where the content shows, fading to clear over `Tokens.Scroll.fade` at an edge with
  /// more beyond it; past that edge (under a bar) the content is hidden while more is there.
  private var fadeMask: some View {
    VStack(spacing: 0) {
      Color.clear.frame(height: edges.above ? edges.insetTop : 0)
      LinearGradient(colors: [.clear, .black], startPoint: .top, endPoint: .bottom)
        .frame(height: edges.above ? Tokens.Scroll.fade : 0)
      Color.black
      LinearGradient(colors: [.black, .clear], startPoint: .top, endPoint: .bottom)
        .frame(height: edges.below ? Tokens.Scroll.fade : 0)
      Color.clear.frame(height: edges.below ? edges.insetBottom : 0)
    }
  }

  private var chevron: some View {
    Button {
      withAnimation(reduceMotion ? nil : Tokens.Motion.standard) {
        position.wrappedValue.scrollTo(y: page.next)
      }
    } label: {
      Image(systemName: Symbols.scrollMore)
        .font(Tokens.Scroll.chevronFont)
        .foregroundStyle(Palette.textTertiary)
        .frame(width: Tokens.Scroll.capsuleWidth, height: Tokens.Scroll.capsuleHeight)
        .background(Palette.surface, in: Capsule())
        .overlay(Capsule().strokeBorder(Palette.separatorStrong, lineWidth: Tokens.Stroke.thin))
        .shadow(color: Palette.shadow.opacity(Tokens.Opacity.faint), radius: Tokens.Size.shadowRadius,
          y: Tokens.Space.xxs)
        .contentShape(Capsule())
    }
    .buttonStyle(.plain)
    .accessibilityLabel(Text(ScrollText.more))
    .hint(ScrollText.more)
  }
}
