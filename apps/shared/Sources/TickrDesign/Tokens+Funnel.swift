import SwiftUI

// Tokens of the one-line funnel and its bars (`FunnelRings`, `FunnelBarRow`) and of the iPhone's
// day switch (`DaySwitch`, `daySwipe`), as on the IOS2Home and IOS2Funnel boards (mockup v155).

extension Tokens.Size {
  /// A stage's ring on the one-line funnel (the board's `.fnd`), and the glyph inside it.
  public static let funnelRing: CGFloat = 26
  public static let funnelGlyph: CGFloat = 13
  /// Height of the one-line funnel's row of rings (the board's `.fnline`).
  public static let funnelLine: CGFloat = 28
  /// A light running along the funnel's rail (the board's `.travel`).
  public static let funnelTraveller: CGFloat = 8
  /// Height of a stage's bar in the funnel sheet (the board's `.fnbar`), and its least width.
  public static let funnelBar: CGFloat = 24
  public static let funnelBarMin: CGFloat = 12
  /// The day button's least height (the board's `.daycap`); its touch target is `touchTarget`.
  public static let dayButton: CGFloat = 36
  /// A row of the day picker (the board's `.dlist > button`).
  public static let dayRow: CGFloat = 48
}

extension Tokens.Opacity {
  /// The funnel's rail in the stages' colours, under the rings (the board's `.fnrail`).
  public static let funnelRail = 0.35
}

extension Tokens.Motion {
  /// One run of a light along the funnel's rail, in seconds, and how many run one after another.
  public static let funnelTravelSeconds = 4.2
  public static let funnelTravellers = 3
  /// Share of a run over which a light fades in at the rail's start and out at its end.
  public static let funnelTravelFade = 0.08
  /// How far a finger moves sideways before the page changes the day (`daySwipe`), in points, and
  /// how many times its vertical move the sideways one must be (a scroll never changes the day).
  public static let daySwipeDistance: CGFloat = 70
  public static let daySwipeDominance: CGFloat = 2
}

extension Tokens {
  /// Proportions of the funnel sheet's bars (`FunnelBarRow`).
  public enum Funnel {
    /// The shortest bar as a share of the widest, so a stage with a few symbols still shows one.
    public static let leastShare: CGFloat = 0.04
  }
}
