import SwiftUI

// MARK: - sheets as tall as their content

public extension View {
  /// A sheet as tall as its content (owner, 2026-11-20: the bot actions opened at half the screen,
  /// scrolled, and the scroll chevron sat on Cancel; "make the window a little bigger so all the
  /// content fits"). Apply it on the sheet's root, around a scroll view that carries
  /// `scrollEdgeHints()` or `scrollEdgeFades()`: the scroll view says how far its content runs
  /// past its frame (`SheetOverflow`), and the sheet takes its own height plus that. Only when
  /// the content is taller than the screen does the sheet stop at full height and scroll, with the
  /// edge hints. On the Mac a sheet already takes its content's size, so this changes nothing there.
  func sheetFitsContent() -> some View {
    modifier(SheetFit())
  }
}

/// How far the sheet's scroll view runs past its visible height, in points (negative when the
/// content is shorter): `SheetFit` hands it down the environment, the scroll view's
/// `ScrollEdgeHints` fills it in, and the sheet grows or shrinks by it.
@MainActor @Observable final class SheetOverflow {
  var value: CGFloat?
}

/// The height a sheet needs: its own height now plus what its scroll view still hides (or minus
/// what it leaves empty). The sum does not depend on the sheet's current height, so one pass fits
/// and later passes ask for the same height. A detent's height excludes the bottom safe area, as
/// a view's own height does.
enum SheetHeight {
  static func needed(sheet: CGFloat, overflow: CGFloat?) -> CGFloat? {
    guard let overflow, sheet > 0 else { return nil }
    return (sheet + overflow).rounded(.up)
  }
}

struct SheetFit: ViewModifier {
  @State private var sheet: CGFloat = 0
  @State private var overflow = SheetOverflow()

  func body(content: Content) -> some View {
    #if os(iOS)
    content
      .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { sheet = $0 }
      .environment(overflow)
      // Until the content is measured (the first layout, before the sheet is in view) the
      // system's half height; then the content's own. Taller than the screen, the system stops
      // it at full height.
      .presentationDetents([detent])
      .presentationDragIndicator(.visible)
    #else
    content
    #endif
  }

  #if os(iOS)
  private var detent: PresentationDetent {
    SheetHeight.needed(sheet: sheet, overflow: overflow.value).map { .height($0) } ?? .medium
  }
  #endif
}
