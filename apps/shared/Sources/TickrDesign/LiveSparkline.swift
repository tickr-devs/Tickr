import Charts
import SwiftUI

/// A small Swift Charts price line without axes that ends in a dot at the latest price. It draws
/// itself in from the left once, as it appears; after that a new price never redraws it: the line
/// glides to its new shape and the dot moves up, down or along with the price. While `live`, each
/// new price pulses the dot, letting a running pulse finish: a moving price beats steadily and a
/// still one rests (Core Animation plays the ring; see PulseLayer). With Reduce Motion everything
/// shows at once and nothing pulses. Fewer than two values draw a flat line. One component for the open
/// position cards of both apps.
public struct LiveSparkline: View {
  /// Plot keys of the two axes (Charts names its values with them; the chart is hidden from
  /// VoiceOver, so they are never read).
  static let indexKey = "index"
  static let valueKey = "value"

  let values: [Double]
  let color: Color
  let live: Bool
  let domain: ClosedRange<Double>?
  let dotSize: CGFloat
  @Environment(\.accessibilityReduceMotion) private var reduceMotion
  @State private var drawn: CGFloat = 0
  @State private var dotShown = false

  /// `domain` is the price range shown; nil spans the values. `live` makes the dot pulse with each
  /// new price.
  public init(values: [Double], color: Color, live: Bool, domain: ClosedRange<Double>? = nil,
    dotSize: CGFloat = Tokens.Size.dotSmall) {
    self.values = values
    self.color = color
    self.live = live
    self.domain = domain
    self.dotSize = dotSize
  }

  public var body: some View {
    let shown = values.count > 1 ? values : [values.first ?? 0, values.first ?? 0]
    let lastIndex = shown.count - 1
    let last = shown[lastIndex]
    Chart(Array(shown.enumerated()), id: \.offset) { point in
      LineMark(x: .value(Self.indexKey, point.offset), y: .value(Self.valueKey, point.element))
        .foregroundStyle(color)
        .lineStyle(StrokeStyle(lineWidth: Tokens.Stroke.bold, lineCap: .round, lineJoin: .round))
    }
    .chartXAxis(.hidden)
    .chartYAxis(.hidden)
    .chartLegend(.hidden)
    .chartXScale(domain: 0...lastIndex)
    .chartYScale(domain: domain ?? Self.span(shown))
    // Only the line draws in; the dot sits outside the mask, so its ring is never cut at the edge.
    .chartPlotStyle { plot in plot.reveal(reduceMotion ? 1 : drawn) }
    .chartOverlay { proxy in
      GeometryReader { geometry in
        if let plot = proxy.plotFrame, let point = proxy.position(forX: lastIndex, y: last) {
          let origin = geometry[plot].origin
          PulseDot(color, size: dotSize, pulsing: live, event: last, letsFinish: true)
            .position(x: origin.x + point.x, y: origin.y + point.y)
            .opacity(reduceMotion || dotShown ? 1 : 0)
        }
      }
      .allowsHitTesting(false)
    }
    // A new price: the line and the dot glide to their new places, nothing draws in again.
    .animation(reduceMotion ? nil : Tokens.Motion.standard, value: shown)
    .onAppear {
      // The dot appears where the line ends, once the line has reached it.
      withAnimation(Tokens.Motion.draw) { drawn = 1 }
      withAnimation(Tokens.Motion.quick.delay(Tokens.Motion.drawSeconds)) { dotShown = true }
    }
    .accessibilityHidden(true)
  }

  /// The values' own range; a flat line gets a range around its value so it sits mid-height.
  static func span(_ values: [Double]) -> ClosedRange<Double> {
    let lowest = values.min() ?? 0
    let highest = values.max() ?? 0
    guard highest > lowest else { return (lowest - 1)...(highest + 1) }
    return lowest...highest
  }
}
