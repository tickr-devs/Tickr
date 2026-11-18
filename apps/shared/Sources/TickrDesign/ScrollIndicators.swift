import SwiftUI

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
}
