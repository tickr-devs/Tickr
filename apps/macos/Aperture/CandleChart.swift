import ApertureDesign
import ApertureKit
import SwiftUI

/// Candles with indicator lines, a volume pane and an ATR pane, drawn in one Canvas.
/// Pinch to zoom, drag to pan, hover for the crosshair. Candles grow in when the chart appears,
/// and the newest candle breathes while the day is live.
struct CandleChart: View {
    let candles: [Candle]
    let timeframe: Timeframe
    let layers: ChartLayers
    let markers: ChartMarkers
    let live: Bool
    @Binding var viewport: Viewport

    @State private var hover: CGPoint?
    @State private var appearedAt = Date()
    @State private var dragStart: Viewport?
    @State private var zoomStart: Viewport?
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    /// Seconds the candles take to grow in.
    private static let appearSeconds = 0.9

    var body: some View {
        let lines = IndicatorLines(candles: candles)
        GeometryReader { proxy in
            let layout = ChartLayout(size: proxy.size, layers: layers)
            let window = viewport.range(total: candles.count)
            TimelineView(.animation(paused: reduceMotion || (!live && Date().timeIntervalSince(appearedAt) > Self.appearSeconds))) { context in
                let progress = reduceMotion ? 1 : min(1, context.date.timeIntervalSince(appearedAt) / Self.appearSeconds)
                let pulse = live && !reduceMotion ? (sin(context.date.timeIntervalSinceReferenceDate * 3) + 1) / 2 : 0
                Canvas { graphics, _ in
                    var painter = ChartPainter(graphics: graphics, layout: layout, candles: candles, window: window,
                                               lines: lines, layers: layers, markers: markers, timeframe: timeframe)
                    painter.draw(progress: progress, pulse: pulse, hover: hover)
                }
            }
            .contentShape(Rectangle())
            .onContinuousHover { phase in
                switch phase {
                case .active(let point): hover = point
                case .ended: hover = nil
                }
            }
            .gesture(drag(width: layout.plot.width, window: window))
            .simultaneousGesture(magnify)
        }
        .padding(12)
        .apertureCard()
        .onAppear { appearedAt = Date() }
        .accessibilityElement()
        .accessibilityLabel("Candle chart, \(timeframe.title), \(candles.count) candles")
    }

    private func drag(width: CGFloat, window: Range<Int>) -> some Gesture {
        DragGesture(minimumDistance: 2)
            .onChanged { value in
                let start = dragStart ?? viewport
                if dragStart == nil { dragStart = viewport }
                let perCandle = width / CGFloat(max(window.count, 1))
                let shift = Int((value.translation.width / perCandle).rounded())
                var next = start
                next.visible = start.visible ?? candles.count
                next.offset = min(max(start.offset + shift, 0), max(candles.count - (next.visible ?? candles.count), 0))
                viewport = next
                hover = nil
            }
            .onEnded { _ in dragStart = nil }
    }

    private var magnify: some Gesture {
        MagnifyGesture()
            .onChanged { value in
                let start = zoomStart ?? viewport
                if zoomStart == nil { zoomStart = viewport }
                var next = start
                next.zoom(by: value.magnification, total: candles.count)
                viewport = next
            }
            .onEnded { _ in zoomStart = nil }
    }
}

/// Indicator values, one per candle.
struct IndicatorLines {
    let vwap: [Double?]
    let fastEMA: [Double?]
    let slowEMA: [Double?]
    let atr: [Double?]

    init(candles: [Candle]) {
        vwap = Indicators.vwap(candles)
        fastEMA = Indicators.ema(candles, period: Indicators.fastEMAPeriod)
        slowEMA = Indicators.ema(candles, period: Indicators.slowEMAPeriod)
        atr = Indicators.atr(candles, period: Indicators.atrPeriod)
    }
}

/// Where each pane sits inside the chart.
struct ChartLayout {
    /// Right gutter for price labels and bottom gutter for times, in points.
    static let priceGutter: CGFloat = 64
    static let timeGutter: CGFloat = 22
    static let paneGap: CGFloat = 10
    /// Share of the height the volume and ATR panes take when shown.
    static let volumeShare: CGFloat = 0.18
    static let atrShare: CGFloat = 0.16

    let plot: CGRect
    let price: CGRect
    let volume: CGRect?
    let atr: CGRect?

    init(size: CGSize, layers: ChartLayers) {
        let width = max(size.width - Self.priceGutter, 1)
        let height = max(size.height - Self.timeGutter, 1)
        let volumeHeight = layers.volume ? height * Self.volumeShare : 0
        let atrHeight = layers.atr ? height * Self.atrShare : 0
        let gaps = (layers.volume ? Self.paneGap : 0) + (layers.atr ? Self.paneGap : 0)
        let priceHeight = max(height - volumeHeight - atrHeight - gaps, 1)
        plot = CGRect(x: 0, y: 0, width: width, height: height)
        price = CGRect(x: 0, y: 0, width: width, height: priceHeight)
        var y = priceHeight
        if layers.volume {
            y += Self.paneGap
            volume = CGRect(x: 0, y: y, width: width, height: volumeHeight)
            y += volumeHeight
        } else {
            volume = nil
        }
        if layers.atr {
            y += Self.paneGap
            atr = CGRect(x: 0, y: y, width: width, height: atrHeight)
        } else {
            atr = nil
        }
    }
}

/// Draws one frame. Kept apart from the view so each step is small and plainly typed.
struct ChartPainter {
    let graphics: GraphicsContext
    let layout: ChartLayout
    let candles: [Candle]
    let window: Range<Int>
    let lines: IndicatorLines
    let layers: ChartLayers
    let markers: ChartMarkers
    let timeframe: Timeframe

    private var low: Double = 0
    private var high: Double = 1

    /// Room above and below the highest and lowest price shown, as a share of the range.
    static let pricePadding = 0.08
    /// Horizontal grid lines in the price pane.
    static let priceGridLines = 5

    init(graphics: GraphicsContext, layout: ChartLayout, candles: [Candle], window: Range<Int>, lines: IndicatorLines,
         layers: ChartLayers, markers: ChartMarkers, timeframe: Timeframe) {
        self.graphics = graphics
        self.layout = layout
        self.candles = candles
        self.window = window
        self.lines = lines
        self.layers = layers
        self.markers = markers
        self.timeframe = timeframe
        var lowest = Double.greatestFiniteMagnitude
        var highest = -Double.greatestFiniteMagnitude
        for index in window {
            lowest = min(lowest, candles[index].low)
            highest = max(highest, candles[index].high)
        }
        for level in markers.levels where Self.levelVisible(level, candles: candles, window: window) {
            lowest = min(lowest, level.price)
            highest = max(highest, level.price)
        }
        if lowest > highest { lowest = 0; highest = 1 }
        let pad = max((highest - lowest) * Self.pricePadding, highest * 0.0005)
        low = lowest - pad
        high = highest + pad
    }

    // MARK: coordinates

    var slot: CGFloat { layout.plot.width / CGFloat(max(window.count, 1)) }

    func x(_ index: Int) -> CGFloat { (CGFloat(index - window.lowerBound) + 0.5) * slot }

    func y(_ price: Double) -> CGFloat {
        let fraction = (price - low) / (high - low)
        return layout.price.maxY - CGFloat(fraction) * layout.price.height
    }

    func price(atY y: CGFloat) -> Double {
        let fraction = Double((layout.price.maxY - y) / layout.price.height)
        return low + fraction * (high - low)
    }

    /// Index of the candle that contains `date` (the last one starting at or before it).
    func index(of date: Date) -> Int? {
        var found: Int?
        for (index, candle) in candles.enumerated() where candle.start <= date { found = index }
        return found
    }

    static func levelVisible(_ level: ChartMarkers.Level, candles: [Candle], window: Range<Int>) -> Bool {
        guard !window.isEmpty else { return false }
        let first = candles[window.lowerBound].start
        let last = candles[window.upperBound - 1].start
        return level.from <= last && (level.to ?? .distantFuture) >= first
    }

    // MARK: drawing

    mutating func draw(progress: Double, pulse: Double, hover: CGPoint?) {
        guard !window.isEmpty else { return }
        drawGrid()
        drawTimeAxis()
        if let pane = layout.volume { drawVolume(in: pane, progress: progress) }
        if let pane = layout.atr { drawATR(in: pane) }
        drawLevels()
        drawCandles(progress: progress, pulse: pulse)
        if layers.vwap { drawLine(lines.vwap, color: Palette.amber, width: 1.6, dash: []) }
        if layers.fastEMA { drawLine(lines.fastEMA, color: Palette.teal, width: 1.3, dash: []) }
        if layers.slowEMA { drawLine(lines.slowEMA, color: Palette.violet, width: 1.3, dash: []) }
        drawMarks(progress: progress)
        drawLastPrice()
        if let hover { drawCrosshair(at: hover) }
    }

    private func drawGrid() {
        let count = Self.priceGridLines
        for step in 0...count {
            let value = low + (high - low) * Double(step) / Double(count)
            let lineY = y(value)
            var path = Path()
            path.move(to: CGPoint(x: 0, y: lineY))
            path.addLine(to: CGPoint(x: layout.plot.width, y: lineY))
            graphics.stroke(path, with: .color(Palette.line), lineWidth: 0.5)
            label(Format.price(Decimal(value)), at: CGPoint(x: layout.plot.width + 6, y: lineY), anchor: .leading,
                  color: Palette.dim)
        }
    }

    private func drawTimeAxis() {
        let stepCandles = max(1, Int((90 / slot).rounded(.up)))
        var index = window.lowerBound
        while index < window.upperBound {
            let labelX = x(index)
            var path = Path()
            path.move(to: CGPoint(x: labelX, y: 0))
            path.addLine(to: CGPoint(x: labelX, y: layout.plot.height))
            graphics.stroke(path, with: .color(Palette.line.opacity(0.6)), lineWidth: 0.5)
            label(Format.marketMinute(candles[index].start), at: CGPoint(x: labelX, y: layout.plot.height + 11),
                  anchor: .center, color: Palette.dim)
            index += stepCandles
        }
    }

    private func drawCandles(progress: Double, pulse: Double) {
        let body = max(1, slot * 0.66)
        for index in window {
            let candle = candles[index]
            // Candles grow from their middle, left to right, as the chart appears.
            let delay = Double(index - window.lowerBound) / Double(max(window.count, 1)) * 0.5
            let grow = CGFloat(min(1, max(0, (progress - delay) / 0.5)))
            let color = candle.isUp ? Palette.green : Palette.red
            let centerX = x(index)
            let mid = (y(candle.open) + y(candle.close)) / 2
            var wick = Path()
            wick.move(to: CGPoint(x: centerX, y: mid + (y(candle.high) - mid) * grow))
            wick.addLine(to: CGPoint(x: centerX, y: mid + (y(candle.low) - mid) * grow))
            graphics.stroke(wick, with: .color(color), lineWidth: 1)
            let top = mid + (min(y(candle.open), y(candle.close)) - mid) * grow
            let bottom = mid + (max(y(candle.open), y(candle.close)) - mid) * grow
            let rect = CGRect(x: centerX - body / 2, y: top, width: body, height: max(1, bottom - top))
            graphics.fill(Path(roundedRect: rect, cornerRadius: min(1.5, body / 4)), with: .color(color))
            if index == candles.count - 1, pulse > 0 {
                let glow = rect.insetBy(dx: -3 - 3 * pulse, dy: -3 - 3 * pulse)
                graphics.stroke(Path(roundedRect: glow, cornerRadius: 3), with: .color(color.opacity(0.5 * (1 - pulse))),
                                lineWidth: 1.5)
            }
        }
    }

    private func drawVolume(in pane: CGRect, progress: Double) {
        var top = 0.0
        for index in window { top = max(top, candles[index].volume) }
        guard top > 0 else { return }
        let body = max(1, slot * 0.66)
        for index in window {
            let candle = candles[index]
            let height = pane.height * CGFloat(candle.volume / top) * CGFloat(progress)
            let rect = CGRect(x: x(index) - body / 2, y: pane.maxY - height, width: body, height: height)
            graphics.fill(Path(rect), with: .color((candle.isUp ? Palette.green : Palette.red).opacity(0.35)))
        }
        label("VOL", at: CGPoint(x: 4, y: pane.minY + 7), anchor: .leading, color: Palette.dim)
        label(Self.compact(top), at: CGPoint(x: layout.plot.width + 6, y: pane.minY + 6), anchor: .leading, color: Palette.dim)
    }

    private func drawATR(in pane: CGRect) {
        var top = 0.0
        for index in window { top = max(top, lines.atr[index] ?? 0) }
        guard top > 0 else {
            label("ATR \(Indicators.atrPeriod): not enough candles yet", at: CGPoint(x: 4, y: pane.midY), anchor: .leading,
                  color: Palette.dim)
            return
        }
        var path = Path()
        var started = false
        for index in window {
            guard let value = lines.atr[index] else { continue }
            let point = CGPoint(x: x(index), y: pane.maxY - pane.height * CGFloat(value / top))
            if started { path.addLine(to: point) } else { path.move(to: point); started = true }
        }
        graphics.stroke(path, with: .color(Palette.pink), lineWidth: 1.3)
        label("ATR \(Indicators.atrPeriod)", at: CGPoint(x: 4, y: pane.minY + 7), anchor: .leading, color: Palette.pinkStrong)
        label(Format.price(Decimal(top)), at: CGPoint(x: layout.plot.width + 6, y: pane.minY + 6), anchor: .leading,
              color: Palette.dim)
    }

    private func drawLine(_ values: [Double?], color: Color, width: CGFloat, dash: [CGFloat]) {
        var path = Path()
        var started = false
        for index in window {
            guard let value = values[index] else { continue }
            let point = CGPoint(x: x(index), y: y(value))
            if started { path.addLine(to: point) } else { path.move(to: point); started = true }
        }
        var clipped = graphics
        clipped.clip(to: Path(layout.price))
        clipped.stroke(path, with: .color(color), style: StrokeStyle(lineWidth: width, lineJoin: .round, dash: dash))
    }

    private func drawLevels() {
        for level in markers.levels where Self.levelVisible(level, candles: candles, window: window) {
            let startX = index(of: level.from).map { max(0, x($0)) } ?? 0
            let endX = level.to.flatMap { index(of: $0) }.map { min(layout.plot.width, x($0)) } ?? layout.plot.width
            let levelY = y(level.price)
            var path = Path()
            path.move(to: CGPoint(x: startX, y: levelY))
            path.addLine(to: CGPoint(x: max(endX, startX + 1), y: levelY))
            let color = level.isStop ? Palette.red : Palette.blue
            graphics.stroke(path, with: .color(color.opacity(0.85)), style: StrokeStyle(lineWidth: 1, dash: [5, 4]))
            label(level.label, at: CGPoint(x: max(endX, startX + 1) - 4, y: levelY - 8), anchor: .trailing, color: color)
        }
    }

    private func drawMarks(progress: Double) {
        guard progress > 0.6 else { return }
        let alpha = min(1, (progress - 0.6) / 0.4)
        for mark in markers.marks {
            guard let index = index(of: mark.time), window.contains(index) else { continue }
            let center = CGPoint(x: x(index), y: y(mark.price))
            let color: Color
            switch mark.kind {
            case .entry: color = Palette.blue
            case .exitWin: color = Palette.green
            case .exitLoss: color = Palette.red
            }
            let size: CGFloat = 7
            var triangle = Path()
            if mark.kind == .entry {
                // Below the price, pointing up at it.
                triangle.move(to: CGPoint(x: center.x, y: center.y + 3))
                triangle.addLine(to: CGPoint(x: center.x - size, y: center.y + 3 + size * 1.4))
                triangle.addLine(to: CGPoint(x: center.x + size, y: center.y + 3 + size * 1.4))
            } else {
                // Above the price, pointing down at it.
                triangle.move(to: CGPoint(x: center.x, y: center.y - 3))
                triangle.addLine(to: CGPoint(x: center.x - size, y: center.y - 3 - size * 1.4))
                triangle.addLine(to: CGPoint(x: center.x + size, y: center.y - 3 - size * 1.4))
            }
            triangle.closeSubpath()
            graphics.fill(triangle, with: .color(color.opacity(alpha)))
            let labelY = mark.kind == .entry ? center.y + 3 + size * 1.4 + 9 : center.y - 3 - size * 1.4 - 9
            label(mark.label, at: CGPoint(x: center.x, y: labelY), anchor: .center, color: color.opacity(alpha), bold: true)
        }
    }

    private func drawLastPrice() {
        guard let last = candles.last else { return }
        let lastY = y(last.close)
        guard lastY >= layout.price.minY, lastY <= layout.price.maxY else { return }
        let color = last.isUp ? Palette.green : Palette.red
        let rect = CGRect(x: layout.plot.width + 2, y: lastY - 9, width: ChartLayout.priceGutter - 4, height: 18)
        graphics.fill(Path(roundedRect: rect, cornerRadius: 4), with: .color(color))
        label(Format.price(Decimal(last.close)), at: CGPoint(x: rect.midX, y: lastY), anchor: .center, color: .white, bold: true)
    }

    private func drawCrosshair(at point: CGPoint) {
        guard layout.plot.contains(point) else { return }
        let index = min(max(window.lowerBound + Int(point.x / slot), window.lowerBound), window.upperBound - 1)
        let snappedX = x(index)
        var path = Path()
        path.move(to: CGPoint(x: snappedX, y: 0))
        path.addLine(to: CGPoint(x: snappedX, y: layout.plot.height))
        if layout.price.contains(point) {
            path.move(to: CGPoint(x: 0, y: point.y))
            path.addLine(to: CGPoint(x: layout.plot.width, y: point.y))
            let tag = CGRect(x: layout.plot.width + 2, y: point.y - 9, width: ChartLayout.priceGutter - 4, height: 18)
            graphics.fill(Path(roundedRect: tag, cornerRadius: 4), with: .color(Palette.text2))
            label(Format.price(Decimal(price(atY: point.y))), at: CGPoint(x: tag.midX, y: point.y), anchor: .center,
                  color: Palette.card, bold: true)
        }
        graphics.stroke(path, with: .color(Palette.muted.opacity(0.7)), style: StrokeStyle(lineWidth: 0.8, dash: [3, 3]))
        drawReadout(for: index)
    }

    /// OHLC, volume and indicator values of the candle under the crosshair, top left.
    private func drawReadout(for index: Int) {
        let candle = candles[index]
        var parts: [(String, Color)] = [
            (Format.marketMinute(candle.start) + " " + timeframe.title, Palette.text2),
            ("O " + Format.price(Decimal(candle.open)), Palette.muted),
            ("H " + Format.price(Decimal(candle.high)), Palette.muted),
            ("L " + Format.price(Decimal(candle.low)), Palette.muted),
            ("C " + Format.price(Decimal(candle.close)), candle.isUp ? Palette.greenStrong : Palette.redStrong),
            ("V " + Self.compact(candle.volume), Palette.muted),
        ]
        if layers.vwap, let value = lines.vwap[index] { parts.append(("VWAP " + Format.price(Decimal(value)), Palette.amberStrong)) }
        if layers.fastEMA, let value = lines.fastEMA[index] {
            parts.append(("EMA\(Indicators.fastEMAPeriod) " + Format.price(Decimal(value)), Palette.tealStrong))
        }
        if layers.slowEMA, let value = lines.slowEMA[index] {
            parts.append(("EMA\(Indicators.slowEMAPeriod) " + Format.price(Decimal(value)), Palette.violetStrong))
        }
        if layers.atr, let value = lines.atr[index] { parts.append(("ATR " + Format.price(Decimal(value)), Palette.pinkStrong)) }
        var cursor: CGFloat = 6
        for (text, color) in parts {
            let resolved = graphics.resolve(Text(text).font(.system(size: 11, weight: .medium, design: .monospaced)).foregroundColor(color))
            let size = resolved.measure(in: CGSize(width: 400, height: 20))
            graphics.fill(Path(roundedRect: CGRect(x: cursor - 3, y: 4, width: size.width + 6, height: size.height + 4), cornerRadius: 4),
                          with: .color(Palette.card.opacity(0.85)))
            graphics.draw(resolved, at: CGPoint(x: cursor, y: 6), anchor: .topLeading)
            cursor += size.width + 10
        }
    }

    private func label(_ text: String, at point: CGPoint, anchor: UnitPoint, color: Color, bold: Bool = false) {
        let styled = Text(text)
            .font(.system(size: 10, weight: bold ? .bold : .regular, design: .monospaced))
            .foregroundColor(color)
        graphics.draw(styled, at: point, anchor: anchor)
    }

    /// 1234567 → "1.23M".
    static func compact(_ value: Double) -> String {
        value.formatted(.number.notation(.compactName).precision(.fractionLength(0...2)))
    }
}
