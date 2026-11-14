import XCTest
@testable import ApertureKit

final class CandlesTests: XCTestCase {
    /// 2026-11-13 09:30 New York (14:30 UTC).
    let open = Date(timeIntervalSince1970: 1_794_580_200)

    func candle(_ minute: Int, _ o: Double, _ h: Double, _ l: Double, _ c: Double, _ v: Double = 100) -> Candle {
        Candle(start: open.addingTimeInterval(Double(minute * 60)), open: o, high: h, low: l, close: c, volume: v)
    }

    func testM5GroupsFromTheHour() {
        let m1 = (0..<7).map { candle($0, Double(10 + $0), Double(11 + $0), Double(9 + $0), Double(10 + $0) + 0.5) }
        let m5 = Timeframe.m5.group(m1)
        XCTAssertEqual(m5.count, 2)
        XCTAssertEqual(m5[0].start, open)
        XCTAssertEqual(m5[0].open, 10)
        XCTAssertEqual(m5[0].high, 15)
        XCTAssertEqual(m5[0].low, 9)
        XCTAssertEqual(m5[0].close, 14.5)
        XCTAssertEqual(m5[0].volume, 500)
        XCTAssertEqual(m5[1].start, open.addingTimeInterval(300))
        XCTAssertEqual(m5[1].volume, 200)
    }

    func testM15KeepsAGapAsSeparateCandles() {
        let m1 = [candle(0, 1, 2, 1, 2), candle(20, 2, 3, 2, 3)]
        XCTAssertEqual(Timeframe.m15.group(m1).map(\.start), [open, open.addingTimeInterval(900)])
        XCTAssertEqual(Timeframe.m1.group(m1), m1)
    }

    func testVWAPWeightsTypicalPriceByVolume() {
        let bars = [candle(0, 10, 12, 9, 12, 100), candle(1, 12, 15, 12, 15, 300), candle(2, 15, 15, 15, 15, 0)]
        let vwap = Indicators.vwap(bars)
        XCTAssertEqual(try XCTUnwrap(vwap[0]), 11, accuracy: 1e-9)
        XCTAssertEqual(try XCTUnwrap(vwap[1]), (11 * 100 + 14 * 300) / 400.0, accuracy: 1e-9)
        XCTAssertEqual(vwap[2], vwap[1])
        XCTAssertNil(Indicators.vwap([candle(0, 1, 1, 1, 1, 0)])[0])
    }

    func testEMASeedsWithSimpleAverage() {
        let bars = [1.0, 2, 3, 4, 5].enumerated().map { candle($0.offset, $0.element, $0.element, $0.element, $0.element) }
        let ema = Indicators.ema(bars, period: 3)
        XCTAssertNil(ema[0])
        XCTAssertNil(ema[1])
        XCTAssertEqual(try XCTUnwrap(ema[2]), 2, accuracy: 1e-9)
        XCTAssertEqual(try XCTUnwrap(ema[3]), 3, accuracy: 1e-9)
        XCTAssertEqual(try XCTUnwrap(ema[4]), 4, accuracy: 1e-9)
        XCTAssertEqual(Indicators.ema(Array(bars.prefix(2)), period: 3), [nil, nil])
    }

    func testATRUsesTrueRangeAndWilderSmoothing() {
        let bars = [candle(0, 10, 11, 9, 10), candle(1, 10, 14, 10, 13), candle(2, 13, 13, 8, 9), candle(3, 9, 10, 9, 10)]
        // True ranges: 2, max(4, 4, 0) = 4, max(5, 0, 5) = 5, max(1, 1, 0) = 1.
        let atr = Indicators.atr(bars, period: 2)
        XCTAssertNil(atr[0])
        XCTAssertEqual(try XCTUnwrap(atr[1]), 3, accuracy: 1e-9)
        XCTAssertEqual(try XCTUnwrap(atr[2]), 4, accuracy: 1e-9)
        XCTAssertEqual(try XCTUnwrap(atr[3]), 2.5, accuracy: 1e-9)
    }
}
