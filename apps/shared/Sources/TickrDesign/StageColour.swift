import SwiftUI
import TickrKit

/// The one colour of each live-flow stage (mockup v151, owner-approved 2026-11-25): nine stages, nine
/// hues, in the order a symbol passes them. Mac Overview and iOS Home read this map, never a colour
/// of their own. A stage whose state can change its colour (the broker stop, the reconciliation)
/// takes this one while healthy and the app's tone of the state otherwise.
public extension PipelineStage {
  /// Found teal, Stocks pink, Priced cyan, Liquid blue, Model violet, Risk amber, Bought orange,
  /// Protected green, Checked lime.
  var fill: Color {
    switch self {
    case .screeners: Palette.accent
    case .universe: Palette.seriesUniverse
    case .quote: Palette.cyan
    case .bars: Palette.info
    case .strategy: Palette.highlight
    case .risk: Palette.warning
    case .order: Palette.orange
    case .stop: Palette.profit
    case .reconcile: Palette.lime
    }
  }

  /// The same hue for words (the stage's name), 4.5:1 on `surface`.
  var text: Color {
    switch self {
    case .screeners: Palette.accentText
    case .universe: Palette.seriesATRText
    case .quote: Palette.cyanText
    case .bars: Palette.infoText
    case .strategy: Palette.highlightText
    case .risk: Palette.warningText
    case .order: Palette.orangeText
    case .stop: Palette.profitText
    case .reconcile: Palette.limeText
    }
  }
}
