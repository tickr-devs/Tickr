@testable import ApertureKit
import XCTest

final class CandlesTests: XCTestCase {
  /// An M1 bar starting `minute` minutes after 15:00 UTC, with the milliseconds the wire carries.
  func bar(
    _ minute: Int, _ open: String, _ high: String, _ low: String, _ close: String, _ volume: String
  ) throws -> BarV1 {
    BarV1(
      start: try WireTime(String(format: "2026-11-16T15:%02d:00.123Z", minute)),
      open: try DecimalString(open), high: try DecimalString(high), low: try DecimalString(low),
      close: try DecimalString(close), volume: try DecimalString(volume))
  }

  func testM1KeepsEveryBarFromTheFixture() throws {
    let bars = try Wire.decoder().decode(BarsV1.self, from: fixture("bars-v1.json")).bars
    let candles = Candles.make(bars, timeframe: .m1)
    XCTAssertEqual(candles.count, bars.count)
    XCTAssertEqual(candles[0].open, 12.30, accuracy: 1e-9)
    XCTAssertEqual(candles[0].close, 12.36, accuracy: 1e-9)
    XCTAssertEqual(candles[0].end.timeIntervalSince(candles[0].start), 60)
    XCTAssertEqual(candles[0].start, try WireTime("2026-11-16T15:00:00.000Z").date, "floored to the minute")
  }

  func testM5MergesBarsInsideEachFiveMinutesAndKeepsGaps() throws {
    let bars = [
      try bar(3, "10", "11", "9.5", "10.5", "100"),
      try bar(4, "10.5", "12", "10", "11", "50"),
      try bar(5, "11", "11.2", "10.8", "11.1", "10"),
      try bar(16, "12", "12", "12", "12", "5"),
    ]
    let candles = Candles.make(bars, timeframe: .m5)
    XCTAssertEqual(candles.count, 3, "15:00, 15:05 and 15:15; no candle for the empty 15:10")
    XCTAssertEqual(candles[0].open, 10)
    XCTAssertEqual(candles[0].high, 12)
    XCTAssertEqual(candles[0].low, 9.5)
    XCTAssertEqual(candles[0].close, 11)
    XCTAssertEqual(candles[0].volume, 150)
    XCTAssertEqual(candles[0].start, try WireTime("2026-11-16T15:00:00.000Z").date)
    XCTAssertEqual(candles[2].start, try WireTime("2026-11-16T15:15:00.000Z").date)
    XCTAssertTrue(candles[0].isUp)
  }

  func testVWAPWeightsTypicalPriceByVolumeAndWaitsForVolume() throws {
    let candles = Candles.make([
      try bar(0, "10", "10", "10", "10", "0"),
      try bar(1, "10", "12", "9", "12", "100"),
      try bar(2, "12", "14", "11", "14", "300"),
    ], timeframe: .m1)
    let vwap = Indicators.vwap(candles)
    XCTAssertNil(vwap[0])
    XCTAssertEqual(try XCTUnwrap(vwap[1]), 11, accuracy: 1e-9)
    XCTAssertEqual(try XCTUnwrap(vwap[2]), (11 * 100 + 13 * 300) / 400, accuracy: 1e-9)
  }

  func testEMAStartsFromTheSimpleAverageThenSmooths() throws {
    let closes = ["1", "2", "3", "4", "5"]
    let bars = try closes.enumerated().map { try bar($0.offset, $0.element, $0.element, $0.element, $0.element, "1") }
    let candles = Candles.make(bars, timeframe: .m1)
    let ema = Indicators.ema(candles, period: 3)
    XCTAssertNil(ema[0])
    XCTAssertNil(ema[1])
    XCTAssertEqual(try XCTUnwrap(ema[2]), 2, accuracy: 1e-9)
    XCTAssertEqual(try XCTUnwrap(ema[3]), 3, accuracy: 1e-9)
    XCTAssertEqual(try XCTUnwrap(ema[4]), 4, accuracy: 1e-9)
    XCTAssertEqual(Indicators.ema(candles, period: 0).compactMap { $0 }.count, 0)
    XCTAssertEqual(Indicators.ema(Array(candles.prefix(2)), period: 3).compactMap { $0 }.count, 0)
  }
}
