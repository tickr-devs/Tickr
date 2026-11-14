import ApertureDesign
import ApertureKit
import SwiftUI

/// Chart section: candles of one symbol on the shown day with indicators, zoom, pan, a
/// crosshair and the day's trades marked on it.
struct ChartView: View {
    @Environment(AppModel.self) private var model
    @AppStorage("chart.timeframe") private var timeframeRaw = Timeframe.m1.rawValue
    @AppStorage("chart.vwap") private var showVWAP = true
    @AppStorage("chart.emaFast") private var showFastEMA = true
    @AppStorage("chart.emaSlow") private var showSlowEMA = true
    @AppStorage("chart.volume") private var showVolume = true
    @AppStorage("chart.atr") private var showATR = false
    @State private var viewport = Viewport()

    private var timeframe: Timeframe { Timeframe(rawValue: timeframeRaw) ?? .m1 }

    var body: some View {
        VStack(spacing: 14) {
            controls.rise(0)
            content
                .rise(0.08)
        }
        .padding(20)
        .task { await model.loadChart() }
        .onChange(of: model.shownChartSymbol) { viewport = Viewport() }
        .onChange(of: timeframeRaw) { viewport = Viewport() }
    }

    // MARK: controls

    private var controls: some View {
        @Bindable var model = model
        return HStack(spacing: 14) {
            Picker("Symbol", selection: Binding(get: { model.shownChartSymbol ?? "" }, set: { model.chartSymbol = $0 })) {
                ForEach(model.chartSymbols, id: \.self) { symbol in Text(symbol).tag(symbol) }
            }
            .frame(width: 150)
            .disabled(model.chartSymbols.isEmpty)
            Picker("Timeframe", selection: $timeframeRaw) {
                ForEach(Timeframe.allCases) { frame in Text(frame.title).tag(frame.rawValue) }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .frame(width: 150)
            Divider().frame(height: 18)
            Toggle("VWAP", isOn: $showVWAP)
            Toggle("EMA \(Indicators.fastEMAPeriod)", isOn: $showFastEMA)
            Toggle("EMA \(Indicators.slowEMAPeriod)", isOn: $showSlowEMA)
            Toggle("Volume", isOn: $showVolume)
            Toggle("ATR \(Indicators.atrPeriod)", isOn: $showATR)
            Spacer()
            ControlGroup {
                Button { viewport.zoom(by: 1 / Viewport.zoomStep) } label: { Label("Zoom Out", systemImage: "minus.magnifyingglass") }
                    .keyboardShortcut("-", modifiers: .command)
                Button { viewport = Viewport() } label: { Label("Fit", systemImage: "arrow.left.and.right.square") }
                    .keyboardShortcut("0", modifiers: .command)
                Button { viewport.zoom(by: Viewport.zoomStep) } label: { Label("Zoom In", systemImage: "plus.magnifyingglass") }
                    .keyboardShortcut("=", modifiers: .command)
            }
            .controlGroupStyle(.navigation)
            .help("Zoom: pinch or ⌘+ / ⌘−. Pan: drag. Fit: ⌘0")
        }
        .toggleStyle(.button)
        .controlSize(.small)
    }

    // MARK: content

    @ViewBuilder private var content: some View {
        if let symbol = model.shownChartSymbol, let bars = model.chartBars, bars.symbol == symbol, !bars.bars.isEmpty {
            let candles = timeframe.group(bars.bars.map(Candle.init))
            CandleChart(
                candles: candles,
                timeframe: timeframe,
                layers: ChartLayers(vwap: showVWAP, fastEMA: showFastEMA, slowEMA: showSlowEMA, volume: showVolume, atr: showATR),
                markers: ChartMarkers.make(trades: model.trades(of: symbol), position: model.positions.first { $0.id == symbol }),
                live: model.isLive,
                viewport: $viewport)
                .id("\(symbol)-\(timeframe.rawValue)-\(model.shownDay.text)")
                .transition(.opacity)
        } else {
            ContentUnavailableView {
                Label(emptyTitle, systemImage: "chart.xyaxis.line")
            } description: {
                Text(emptyDetail)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .apertureCard()
        }
    }

    private var emptyTitle: String {
        if model.chartSymbols.isEmpty { return "No bars this day" }
        if model.chartError != nil { return "Bars unavailable" }
        return "Loading bars…"
    }

    private var emptyDetail: String {
        if model.chartSymbols.isEmpty {
            return "The bot captures M1 bars for the symbols it trades or watches. None were captured on \(Format.dayLabel(model.shownDay))."
        }
        return model.chartError ?? "Reading captured M1 bars from the bot."
    }
}

// MARK: viewport

/// Which candles are on screen: how many, and how far back from the newest the right edge sits.
struct Viewport: Equatable {
    /// Candles visible at once; nil fits the whole day.
    var visible: Int?
    /// Candles hidden to the right of the window (0 = newest candle at the right edge).
    var offset: Int = 0

    static let zoomStep = 1.5
    static let minimumVisible = 12

    mutating func zoom(by factor: Double, total: Int? = nil) {
        let current = Double(visible ?? total ?? 240)
        visible = max(Self.minimumVisible, Int((current / factor).rounded()))
    }

    func range(total: Int) -> Range<Int> {
        let count = min(max(visible ?? total, min(Self.minimumVisible, total)), total)
        let end = max(count, total - min(offset, max(total - count, 0)))
        return (end - count)..<end
    }
}

struct ChartLayers: Equatable {
    var vwap: Bool
    var fastEMA: Bool
    var slowEMA: Bool
    var volume: Bool
    var atr: Bool
}

/// Trades and the open position drawn over the candles.
struct ChartMarkers {
    struct Mark: Identifiable {
        enum Kind { case entry, exitWin, exitLoss }
        let id: String
        let kind: Kind
        let time: Date
        let price: Double
        let label: String
    }

    /// A stop level held from `from` until `to` (nil = still open).
    struct Level: Identifiable {
        let id: String
        let price: Double
        let from: Date
        let to: Date?
        let label: String
        let isStop: Bool
    }

    var marks: [Mark] = []
    var levels: [Level] = []

    static func make(trades: [DayTrade], position: PositionRow?) -> ChartMarkers {
        var markers = ChartMarkers()
        for trade in trades {
            let win = trade.resultGrossUsd.value >= 0
            markers.marks.append(Mark(id: trade.tradeId + "-in", kind: .entry, time: trade.openedAt.date,
                                      price: Candle.double(trade.entryPrice.value), label: "BUY \(Format.quantity(trade.quantity.value))"))
            markers.marks.append(Mark(id: trade.tradeId + "-out", kind: win ? .exitWin : .exitLoss, time: trade.closedAt.date,
                                      price: Candle.double(trade.exitPrice.value),
                                      label: "\(trade.exitReason.uppercased()) \(Money.signed(trade.resultGrossUsd.value))"))
            if let stop = trade.stopPrice {
                markers.levels.append(Level(id: trade.tradeId + "-stop", price: Candle.double(stop.value), from: trade.openedAt.date,
                                            to: trade.closedAt.date, label: "stop", isStop: true))
            }
        }
        if let position {
            markers.marks.append(Mark(id: "open-in", kind: .entry, time: position.position.openedAt.date,
                                      price: Candle.double(position.entry), label: "BUY \(Format.quantity(position.quantity))"))
            markers.levels.append(Level(id: "open-entry", price: Candle.double(position.entry), from: position.position.openedAt.date,
                                        to: nil, label: "entry", isStop: false))
            if let stop = position.stop {
                markers.levels.append(Level(id: "open-stop", price: Candle.double(stop), from: position.position.openedAt.date,
                                            to: nil, label: "stop \(Board.stopTag(position.position.stop.state))", isStop: true))
            }
        }
        return markers
    }
}
