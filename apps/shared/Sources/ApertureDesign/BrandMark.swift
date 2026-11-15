import SwiftUI

/// The Tickr mark: a price line drawn as a tick that breaks upward, with a spark at its end. The
/// app icon and the iOS launch screen draw the same line (apps/scripts/make_brand_assets.py).
///
/// On appear the line draws itself and the area under it fades in. After that it moves only on a
/// real event: each time `event` changes (new bot activity) the spark sends out one ring. Nothing
/// loops; with Reduce Motion the mark stands still.
///
/// Empty screens show it as their illustration (`BrandMark.empty`): in brand colours when there is
/// simply nothing yet, muted when the bot cannot be reached.
public struct BrandMark: View {
  /// How the mark is coloured.
  public enum Tone: Sendable {
    /// Brand colours: profit line, accent area.
    case brand
    /// One quiet colour: nothing can be known right now (bot unreachable).
    case muted
  }

  @Environment(\.accessibilityReduceMotion) private var reduceMotion
  @State private var drawn: CGFloat = 0
  @State private var ringScale: CGFloat = 1
  @State private var ringOpacity = 0.0
  @State private var settled = false
  let size: CGFloat
  /// What the bot has done; a change flashes the spark once. Nil when nothing is known.
  let event: AnyHashable?
  let tone: Tone

  /// Points of the line as shares of the mark's side, left to right (POINTS in make_brand_assets.py).
  static let start = CGPoint(x: 0.205, y: 0.51)
  static let dip = CGPoint(x: 0.39, y: 0.7)
  static let bounce = CGPoint(x: 0.5, y: 0.575)
  static let retest = CGPoint(x: 0.565, y: 0.625)
  public static let breakout = CGPoint(x: 0.81, y: 0.285)
  static let points = [start, dip, bounce, retest, breakout]
  /// Line width, spark diameter and the bottom of the fading area, as shares of the side.
  static let lineWidth = 0.084
  public static let sparkDiameter = 0.09
  static let areaBottom = 0.86

  public init(size: CGFloat = Tokens.Size.logo, event: AnyHashable?, tone: Tone = .brand) {
    self.size = size
    self.event = event
    self.tone = tone
  }

  /// The illustration of an empty screen: large and still.
  public static func empty(_ tone: Tone = .brand) -> BrandMark {
    BrandMark(size: Tokens.Size.emptyMark, event: nil, tone: tone)
  }

  private var lineColor: Color { tone == .brand ? Palette.profit : Palette.textQuaternary }
  private var areaColor: Color { tone == .brand ? Palette.accent : Palette.textQuaternary }
  private var lineEnd: Color { tone == .brand ? Palette.accent : Palette.textQuaternary }

  public var body: some View {
    let spark = Self.breakout
    let diameter = size * Self.sparkDiameter
    ZStack(alignment: .topLeading) {
      TickArea(points: Self.points, bottom: Self.areaBottom)
        .fill(LinearGradient(colors: [areaColor.opacity(Tokens.Opacity.border), areaColor.opacity(0)],
          startPoint: .top, endPoint: .bottom))
        .opacity(drawn)
      TickLine(points: Self.points)
        .trim(from: 0, to: drawn)
        .stroke(
          LinearGradient(colors: [lineColor, lineEnd], startPoint: .topTrailing, endPoint: .bottomLeading),
          style: StrokeStyle(lineWidth: size * Self.lineWidth, lineCap: .round, lineJoin: .round))
      Circle()
        .fill(lineColor)
        .frame(width: diameter, height: diameter)
        .scaleEffect(ringScale)
        .opacity(ringOpacity)
        .position(x: spark.x * size, y: spark.y * size)
      Circle()
        .fill(lineColor)
        .frame(width: diameter, height: diameter)
        .scaleEffect(drawn)
        .position(x: spark.x * size, y: spark.y * size)
    }
    .frame(width: size, height: size)
    .onAppear(perform: appear)
    .onChange(of: event) { flash() }
    .accessibilityHidden(true)
  }

  private func appear() {
    guard !reduceMotion else {
      drawn = 1
      return
    }
    withAnimation(Tokens.Motion.draw) {
      drawn = 1
    } completion: {
      settled = true
    }
  }

  /// One ring from the spark: a real event happened.
  private func flash() {
    guard settled, event != nil, !reduceMotion else { return }
    var reset = Transaction()
    reset.disablesAnimations = true
    withTransaction(reset) {
      ringScale = 1
      ringOpacity = Tokens.Opacity.faint
    }
    withAnimation(.easeOut(duration: Tokens.Motion.pulseSeconds)) {
      ringScale = Tokens.Motion.pulseScale
      ringOpacity = 0
    }
  }
}

/// The tick's price line through `points` (shares of the rect).
struct TickLine: Shape {
  let points: [CGPoint]

  func path(in rect: CGRect) -> Path {
    Path { path in
      path.addLines(points.map { CGPoint(x: rect.minX + $0.x * rect.width, y: rect.minY + $0.y * rect.height) })
    }
  }
}

/// The area under the tick's line, down to `bottom` (a share of the rect's height).
struct TickArea: Shape {
  let points: [CGPoint]
  let bottom: CGFloat

  func path(in rect: CGRect) -> Path {
    guard let first = points.first, let last = points.last else { return Path() }
    var path = TickLine(points: points).path(in: rect)
    path.addLine(to: CGPoint(x: rect.minX + last.x * rect.width, y: rect.minY + bottom * rect.height))
    path.addLine(to: CGPoint(x: rect.minX + first.x * rect.width, y: rect.minY + bottom * rect.height))
    path.closeSubpath()
    return path
  }
}

#Preview {
  HStack {
    BrandMark(event: 1)
    BrandMark.empty()
    BrandMark.empty(.muted)
  }
    .padding()
    .background(Palette.background)
}
