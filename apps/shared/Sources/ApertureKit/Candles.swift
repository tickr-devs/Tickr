import Foundation

/// Chart math shared by the macOS and iOS apps: M1 bars grouped into larger candles, and the
/// indicators drawn over them. Display only: the bot never reads these values, so Double is fine.

/// A candle on the chart, in Double for drawing.
public struct Candle: Hashable, Sendable, Identifiable {
    public let start: Date
    public let open: Double
    public let high: Double
    public let low: Double
    public let close: Double
    public let volume: Double

    public var id: Date { start }
    public var isUp: Bool { close >= open }
    /// (high + low + close) / 3, the price VWAP weights by volume.
    public var typicalPrice: Double { (high + low + close) / 3 }

    public init(start: Date, open: Double, high: Double, low: Double, close: Double, volume: Double) {
        self.start = start
        self.open = open
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume
    }

    public init(_ bar: BarV1) {
        self.init(start: bar.start.date,
                  open: Self.double(bar.open.value), high: Self.double(bar.high.value),
                  low: Self.double(bar.low.value), close: Self.double(bar.close.value),
                  volume: Self.double(bar.volume.value))
    }

    public static func double(_ value: Decimal) -> Double { NSDecimalNumber(decimal: value).doubleValue }
}

/// Chart timeframes. M1 is what the bot captures; M5 and M15 are grouped from it.
public enum Timeframe: Int, CaseIterable, Identifiable, Sendable {
    case m1 = 1, m5 = 5, m15 = 15

    public var id: Int { rawValue }
    public var minutes: Int { rawValue }
    public var title: String { "M\(rawValue)" }

    /// Groups M1 candles (oldest first) into this timeframe. A group starts at a whole multiple of
    /// the timeframe counted from the hour, so 09:30 starts an M5 and an M15 candle. New York's
    /// offset from UTC is whole hours, so counting from the epoch gives the same boundaries.
    public func group(_ candles: [Candle]) -> [Candle] {
        guard self != .m1 else { return candles }
        let seconds = Double(minutes * 60)
        var result: [Candle] = []
        var bucket: [Candle] = []
        var bucketStart: Double?
        for candle in candles {
            let start = (candle.start.timeIntervalSince1970 / seconds).rounded(.down) * seconds
            if start != bucketStart, !bucket.isEmpty {
                result.append(Self.merge(bucket, start: bucketStart ?? start))
                bucket.removeAll()
            }
            bucketStart = start
            bucket.append(candle)
        }
        if let bucketStart, !bucket.isEmpty { result.append(Self.merge(bucket, start: bucketStart)) }
        return result
    }

    static func merge(_ bucket: [Candle], start: Double) -> Candle {
        Candle(start: Date(timeIntervalSince1970: start),
               open: bucket[0].open,
               high: bucket.map(\.high).max() ?? bucket[0].high,
               low: bucket.map(\.low).min() ?? bucket[0].low,
               close: bucket[bucket.count - 1].close,
               volume: bucket.map(\.volume).reduce(0, +))
    }
}

/// Indicator lines, one value per candle (nil where a value is not defined yet).
public enum Indicators {
    /// Fast and slow EMA periods drawn on the chart, in candles.
    public static let fastEMAPeriod = 9
    public static let slowEMAPeriod = 20
    /// ATR period, in candles (Wilder's smoothing).
    public static let atrPeriod = 14

    /// Session VWAP: cumulative typical price times volume over cumulative volume, from the
    /// first candle given. nil until some volume has traded.
    public static func vwap(_ candles: [Candle]) -> [Double?] {
        var priceVolume = 0.0
        var volume = 0.0
        return candles.map { candle in
            priceVolume += candle.typicalPrice * candle.volume
            volume += candle.volume
            return volume > 0 ? priceVolume / volume : nil
        }
    }

    /// Exponential moving average of the closes, seeded with the simple average of the first
    /// `period` closes. nil before that.
    public static func ema(_ candles: [Candle], period: Int) -> [Double?] {
        guard period > 0, candles.count >= period else { return Array(repeating: nil, count: candles.count) }
        let alpha = 2.0 / Double(period + 1)
        var result: [Double?] = Array(repeating: nil, count: period - 1)
        var value = candles[0..<period].map(\.close).reduce(0, +) / Double(period)
        result.append(value)
        for candle in candles[period...] {
            value += alpha * (candle.close - value)
            result.append(value)
        }
        return result
    }

    /// Average true range with Wilder's smoothing, seeded with the simple average of the first
    /// `period` true ranges. The first candle's true range is its high minus low.
    public static func atr(_ candles: [Candle], period: Int) -> [Double?] {
        guard period > 0, candles.count >= period else { return Array(repeating: nil, count: candles.count) }
        var ranges: [Double] = []
        for (index, candle) in candles.enumerated() {
            if index == 0 {
                ranges.append(candle.high - candle.low)
            } else {
                let previousClose = candles[index - 1].close
                ranges.append(max(candle.high - candle.low, abs(candle.high - previousClose), abs(candle.low - previousClose)))
            }
        }
        var result: [Double?] = Array(repeating: nil, count: period - 1)
        var value = ranges[0..<period].reduce(0, +) / Double(period)
        result.append(value)
        for range in ranges[period...] {
            value = (value * Double(period - 1) + range) / Double(period)
            result.append(value)
        }
        return result
    }
}
