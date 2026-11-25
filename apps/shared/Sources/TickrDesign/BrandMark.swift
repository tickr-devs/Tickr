import SwiftUI

/// The Tickr mark, as the tickr.bot site draws it: a teal price line that dips, bounces and breaks
/// out to a dot, on a dark rounded tile with a soft teal halo. The app icon and the iOS launch
/// screen draw the same tile and line (apps/scripts/make_brand_assets.py).
///
/// On appear the line draws itself and the dot pops in. While `live` (the bot's data is current)
/// the halo then breathes slowly, as on the site; otherwise, and with Reduce Motion, it stands still.
///
/// Empty screens show it as their illustration (`BrandMark.empty`): in brand colours when there is
/// simply nothing yet, muted when the bot cannot be reached.
public struct BrandMark: View {
  /// How the mark is coloured.
  public enum Tone: Sendable {
    /// Brand colours: teal line and halo.
    case brand
    /// A quiet line and no halo: nothing can be known right now (bot unreachable).
    case muted
  }

  @Environment(\.accessibilityReduceMotion) private var reduceMotion
  @Environment(\.scenePhase) private var scenePhase
  @State private var drawn: CGFloat = 0
  @State private var popped: CGFloat = 0
  @State private var settled = false
  let size: CGFloat
  let live: Bool
  let tone: Tone
  /// How the line draws itself on appear; nil shows it drawn at once.
  let drawing: Animation?
  /// How the dot pops in; nil pops it with the line's draw.
  let sparkPop: Animation?

  /// Points of the line as shares of the tile's side, left to right: the site's 24-unit path
  /// M3 15 L8 11 L11 14 L20 5, centred in its 40-unit tile (POINTS in make_brand_assets.py).
  static let start = CGPoint(x: 0.275, y: 0.575)
  static let bounce = CGPoint(x: 0.4, y: 0.475)
  static let retest = CGPoint(x: 0.475, y: 0.55)
  public static let breakout = CGPoint(x: 0.7, y: 0.325)
  static let points = [start, bounce, retest, breakout]
  /// Line width and dot diameter, as shares of the side (2.4 and 2 x 1.6 of 40).
  static let lineWidth = 0.06
  public static let sparkDiameter = 0.08
  /// The tile's corner radius and hairline edge, as shares of its side (12 and 1 of 40).
  static let tileRadius = 0.3
  static let tileEdge = 0.025
  /// The tile's gradient runs a little off vertical (the site's 160 degrees).
  static let tileTop = UnitPoint(x: 0.33, y: 0)
  static let tileBottom = UnitPoint(x: 0.67, y: 1)
  /// The line's 24-unit grid inside the tile: its inset from the tile's edge and its side, as
  /// shares of the tile (8 and 24 of 40). `BrandGlyph` draws the line on the grid alone.
  static let gridInset = 0.2
  static let gridSide = 0.6
  /// The halo's side as a multiple of the tile's: it reaches 8 of 40 past each edge, as on the site.
  static let haloSide = 1.4
  /// Seconds of one breath of the halo (out and back) and how far it grows, as on the site.
  static let haloSeconds = 3.2
  static let haloScale: CGFloat = 1.12
  /// Seconds before the line starts drawing, so a window is on screen before it moves.
  static let drawDelay = 0.35

  /// `drawsIn` false shows the mark already drawn: where an opening animation has just put it.
  public init(size: CGFloat = Tokens.Size.logo, live: Bool, tone: Tone = .brand, drawsIn: Bool = true) {
    self.init(size: size, live: live, tone: tone, drawing: drawsIn ? Tokens.Motion.draw.delay(Self.drawDelay) : nil)
  }

  init(size: CGFloat, live: Bool, tone: Tone, drawing: Animation?, sparkPop: Animation? = nil) {
    self.size = size
    self.live = live
    self.tone = tone
    self.drawing = drawing
    self.sparkPop = sparkPop
  }

  /// The illustration of an empty screen: large and still.
  public static func empty(_ tone: Tone = .brand) -> BrandMark {
    BrandMark(size: Tokens.Size.emptyMark, live: false, tone: tone)
  }

  private var lineColor: Color { tone == .brand ? Palette.brandLine : Palette.brandMuted }
  private var breathes: Bool { tone == .brand && live && settled && !reduceMotion }

  public var body: some View {
    let spark = Self.breakout
    let diameter = size * Self.sparkDiameter
    let halo = size * Self.haloSide
    ZStack(alignment: .topLeading) {
      RoundedRectangle(cornerRadius: size * Self.tileRadius, style: .continuous)
        .fill(LinearGradient(colors: [Palette.brandTileTop, Palette.brandTileBottom],
          startPoint: Self.tileTop, endPoint: Self.tileBottom))
        .strokeBorder(Palette.brandTileEdge, lineWidth: size * Self.tileEdge)
      TickLine(points: Self.points)
        .trim(from: 0, to: drawn)
        .stroke(lineColor, style: StrokeStyle(lineWidth: size * Self.lineWidth, lineCap: .round, lineJoin: .round))
      Circle()
        .fill(lineColor)
        .frame(width: diameter, height: diameter)
        .scaleEffect(sparkPop == nil ? drawn : popped)
        .position(x: spark.x * size, y: spark.y * size)
    }
    .frame(width: size, height: size)
    .background {
      if tone == .brand {
        if breathes {
          // While live the halo breathes (Core Animation runs it; see BrandMarkLive).
          BrandMarkLive(size: halo, color: Palette.brandLine, active: scenePhase == .active)
            .frame(width: halo, height: halo)
            .allowsHitTesting(false)
        } else {
          BrandHalo(color: Palette.brandLine)
            .frame(width: halo, height: halo)
            .opacity(Tokens.Opacity.muted * drawn)
        }
      }
    }
    .onAppear(perform: appear)
    .accessibilityHidden(true)
  }

  private func appear() {
    guard !reduceMotion, let drawing else {
      drawn = 1
      popped = 1
      settled = true
      return
    }
    withAnimation(drawing) {
      drawn = 1
    } completion: {
      settled = true
    }
    if let sparkPop { withAnimation(sparkPop) { popped = 1 } }
  }

}

/// The mark's line and dot alone, without tile or halo, filling its frame with the 24-unit grid:
/// in the current foreground style, for one-colour places such as the Mac menu bar.
public struct BrandGlyph: View {
  /// The glyph's side in the Mac menu bar: a menu-bar extra's image side.
  public static let menuBarSide: CGFloat = 18
  let size: CGFloat

  public init(size: CGFloat) {
    self.size = size
  }

  /// A point of the tile's line in the grid's own shares.
  private static func onGrid(_ point: CGPoint) -> CGPoint {
    CGPoint(x: (point.x - BrandMark.gridInset) / BrandMark.gridSide,
      y: (point.y - BrandMark.gridInset) / BrandMark.gridSide)
  }

  /// Draws the glyph in black into `rect` of a top-left-origin context (an image's drawing).
  public static func draw(in context: CGContext, rect: CGRect) {
    let side = min(rect.width, rect.height)
    let line = TickLine(points: BrandMark.points.map(onGrid)).path(in: rect)
    context.setStrokeColor(CGColor(gray: 0, alpha: 1))
    context.setFillColor(CGColor(gray: 0, alpha: 1))
    context.setLineWidth(side * BrandMark.lineWidth / BrandMark.gridSide)
    context.setLineCap(.round)
    context.setLineJoin(.round)
    context.addPath(line.cgPath)
    context.strokePath()
    let end = onGrid(BrandMark.breakout)
    let dot = side * BrandMark.sparkDiameter / BrandMark.gridSide
    context.fillEllipse(in: CGRect(x: rect.minX + end.x * rect.width - dot / 2,
      y: rect.minY + end.y * rect.height - dot / 2, width: dot, height: dot))
  }

  public var body: some View {
    let dot = size * BrandMark.sparkDiameter / BrandMark.gridSide
    let end = Self.onGrid(BrandMark.breakout)
    ZStack(alignment: .topLeading) {
      TickLine(points: BrandMark.points.map(Self.onGrid))
        .stroke(style: StrokeStyle(lineWidth: size * BrandMark.lineWidth / BrandMark.gridSide,
          lineCap: .round, lineJoin: .round))
      Circle()
        .frame(width: dot, height: dot)
        .position(x: end.x * size, y: end.y * size)
    }
    .frame(width: size, height: size)
    .accessibilityHidden(true)
  }
}

/// The mark's still halo: the site's soft teal glow, strongest in the middle and gone at the edge.
struct BrandHalo: View {
  let color: Color

  var body: some View {
    GeometryReader { proxy in
      RadialGradient(colors: [color.opacity(Tokens.Opacity.faint), color.opacity(0)],
        center: .center, startRadius: 0, endRadius: min(proxy.size.width, proxy.size.height) / 2)
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

#Preview {
  HStack(spacing: Tokens.Space.xl) {
    BrandMark(live: true)
    BrandMark.empty()
    BrandMark.empty(.muted)
    BrandGlyph(size: BrandGlyph.menuBarSide)
  }
    .padding()
    .background(Palette.background)
}
