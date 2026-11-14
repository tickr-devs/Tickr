import Foundation

/// Chart data built from the M1 bars of GET /bars: candles in a chosen timeframe and the indicator
/// lines drawn over them. The API serves M1 only; clients aggregate and compute indicators
/// themselves (docs/api.md, "History"). Values are `Double` because they are only drawn, never
/// sent back or added up as money.

/// Candle length. The raw value is the length in minutes.
public enum Timeframe: Int, CaseIterable, Identifiable, Sendable {
  case m1 = 1
  case m5 = 5
  case m15 = 15

  public var id: Int { rawValue }

  /// Seconds in one minute (unit factor).
  static let secondsPerMinute = 60

  /// Length of one candle in seconds.
  public var seconds: TimeInterval { TimeInterval(rawValue * Self.secondsPerMinute) }
}

/// One candle; `start` is its opening time, `end` the opening time of the next.
public struct Candle: Hashable, Sendable, Identifiable {
  public let start: Date
  public let end: Date
  public let open: Double
  public let high: Double
  public let low: Double
  public let close: Double
  public let volume: Double

  public var id: Date { start }
  /// Whether the candle closed at or above its open.
  public var isUp: Bool { close >= open }
  /// High, low and close averaged: the price VWAP weights by volume.
  public var typicalPrice: Double { (high + low + close) / Self.typicalPriceParts }

  /// Number of prices averaged in the typical price.
  static let typicalPriceParts = 3.0

  public init(start: Date, end: Date, open: Double, high: Double, low: Double, close: Double, volume: Double) {
    self.start = start
    self.end = end
    self.open = open
    self.high = high
    self.low = low
    self.close = close
    self.volume = volume
  }
}

public enum Candles {
  /// Candles of `timeframe` from M1 bars (oldest first, as GET /bars serves them). A candle starts
  /// on a whole multiple of the timeframe (09:35, 09:40 for M5) and holds the bars that start
  /// inside it; a candle without bars is not made, so gaps stay gaps.
  public static func make(_ bars: [BarV1], timeframe: Timeframe) -> [Candle] {
    var candles: [Candle] = []
    for bar in bars {
      let length = timeframe.seconds
      let start = Date(timeIntervalSince1970: (bar.start.date.timeIntervalSince1970 / length).rounded(.down) * length)
      let open = Double(truncating: bar.open.value as NSNumber)
      let high = Double(truncating: bar.high.value as NSNumber)
      let low = Double(truncating: bar.low.value as NSNumber)
      let close = Double(truncating: bar.close.value as NSNumber)
      let volume = Double(truncating: bar.volume.value as NSNumber)
      if let last = candles.last, last.start == start {
        candles[candles.count - 1] = Candle(
          start: start, end: last.end, open: last.open, high: max(last.high, high),
          low: min(last.low, low), close: close, volume: last.volume + volume)
      } else {
        candles.append(Candle(
          start: start, end: start.addingTimeInterval(length), open: open, high: high, low: low,
          close: close, volume: volume))
      }
    }
    return candles
  }
}

/// Indicator lines, one value per candle (nil where the line is not defined yet).
public enum Indicators {
  /// Period of the fast EMA line, in candles.
  public static let fastEMAPeriod = 9
  /// Period of the slow EMA line, in candles.
  public static let slowEMAPeriod = 20
  /// Smoothing factor of an EMA: each new close weighs smoothing / (period + 1).
  static let emaSmoothing = 2.0

  /// Volume-weighted average of the typical price from the first candle given (the day's first
  /// captured bar) up to each candle. Nil until some volume has traded.
  public static func vwap(_ candles: [Candle]) -> [Double?] {
    var priceVolume = 0.0
    var volume = 0.0
    return candles.map { candle in
      priceVolume += candle.typicalPrice * candle.volume
      volume += candle.volume
      return volume > 0 ? priceVolume / volume : nil
    }
  }

  /// Exponential moving average of the closes. The first value is the simple average of the first
  /// `period` closes, at the `period`-th candle; earlier candles have none.
  public static func ema(_ candles: [Candle], period: Int) -> [Double?] {
    guard period > 0 else { return candles.map { _ in nil } }
    let weight = emaSmoothing / Double(period + 1)
    var values: [Double?] = []
    var sum = 0.0
    var last: Double?
    for (index, candle) in candles.enumerated() {
      if let previous = last {
        last = previous + weight * (candle.close - previous)
      } else {
        sum += candle.close
        if index + 1 == period { last = sum / Double(period) }
      }
      values.append(last)
    }
    return values
  }
}
