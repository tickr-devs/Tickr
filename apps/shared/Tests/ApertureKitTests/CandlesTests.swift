@testable import ApertureKit
import Foundation
import Testing

@Suite("Chart candles and indicator lines")
struct CandlesTests {
  /// An M1 bar starting `minute` minutes after 15:00 UTC, with the milliseconds the wire carries.
  func bar(
    _ minute: Int, _ open: String, _ high: String, _ low: String, _ close: String, _ volume: String
  ) throws -> BarV1 {
    BarV1(
      start: try WireTime(String(format: "2026-11-16T15:%02d:00.123Z", minute)),
      open: try DecimalString(open), high: try DecimalString(high), low: try DecimalString(low),
      close: try DecimalString(close), volume: try DecimalString(volume))
  }

  func near(_ value: Double?, _ expected: Double) -> Bool {
    guard let value else { return false }
    return abs(value - expected) < 1e-9
  }

  @Test("M1 keeps every bar of the fixture, floored to the minute")
  func m1KeepsEveryBar() throws {
    let bars = try Wire.decoder().decode(BarsV1.self, from: fixture("bars-v1.json")).bars
    let candles = Candles.make(bars, timeframe: .m1)
    #expect(candles.count == bars.count)
    #expect(near(candles[0].open, 12.30))
    #expect(near(candles[0].close, 12.36))
    #expect(candles[0].end.timeIntervalSince(candles[0].start) == 60)
    #expect(candles[0].start == (try WireTime("2026-11-16T15:00:00.000Z").date))
  }

  @Test("M5 merges the bars inside each five minutes and keeps gaps")
  func m5MergesAndKeepsGaps() throws {
    let bars = [
      try bar(3, "10", "11", "9.5", "10.5", "100"),
      try bar(4, "10.5", "12", "10", "11", "50"),
      try bar(5, "11", "11.2", "10.8", "11.1", "10"),
      try bar(16, "12", "12", "12", "12", "5"),
    ]
    let candles = Candles.make(bars, timeframe: .m5)
    // 15:00, 15:05 and 15:15; no candle for the empty 15:10.
    #expect(candles.count == 3)
    #expect(candles[0].open == 10)
    #expect(candles[0].high == 12)
    #expect(candles[0].low == 9.5)
    #expect(candles[0].close == 11)
    #expect(candles[0].volume == 150)
    #expect(candles[0].isUp)
    #expect(candles[0].start == (try WireTime("2026-11-16T15:00:00.000Z").date))
    #expect(candles[2].start == (try WireTime("2026-11-16T15:15:00.000Z").date))
  }

  @Test("VWAP weights the typical price by volume and waits for volume")
  func vwap() throws {
    let candles = Candles.make([
      try bar(0, "10", "10", "10", "10", "0"),
      try bar(1, "10", "12", "9", "12", "100"),
      try bar(2, "12", "14", "11", "14", "300"),
    ], timeframe: .m1)
    let vwap = Indicators.vwap(candles)
    #expect(vwap[0] == nil)
    #expect(near(vwap[1], 11))
    #expect(near(vwap[2], (11.0 * 100 + 13 * 300) / 400))
  }

  @Test("EMA starts from the simple average of the first period, then smooths")
  func ema() throws {
    let closes = ["1", "2", "3", "4", "5"]
    let bars = try closes.enumerated().map { try bar($0.offset, $0.element, $0.element, $0.element, $0.element, "1") }
    let candles = Candles.make(bars, timeframe: .m1)
    let ema = Indicators.ema(candles, period: 3)
    #expect(ema[0] == nil)
    #expect(ema[1] == nil)
    #expect(near(ema[2], 2))
    #expect(near(ema[3], 3))
    #expect(near(ema[4], 4))
    #expect(Indicators.ema(candles, period: 0).allSatisfy { $0 == nil })
    #expect(Indicators.ema(Array(candles.prefix(2)), period: 3).allSatisfy { $0 == nil })
  }
}
