import Foundation
import Testing
@testable import TickrKit

@Suite("Position marks from bars")
struct PositionMarksTests {
  /// A bar starting `minute` minutes past 15:00 UTC.
  func bar(_ minute: Int, low: String, high: String, close: String) throws -> BarV1 {
    let json = """
      {"start": "2026-11-16T15:\(String(format: "%02d", minute)):00.000Z", "open": "\(close)",
       "high": "\(high)", "low": "\(low)", "close": "\(close)", "volume": "100"}
      """
    return try Wire.decoder().decode(BarV1.self, from: Data(json.utf8))
  }

  func time(_ minute: Int, second: Int = 0) throws -> Date {
    try Wire.decoder().decode(WireTime.self,
      from: Data("\"2026-11-16T15:\(String(format: "%02d", minute)):\(String(format: "%02d", second)).000Z\"".utf8)).date
  }

  @Test("Bars from the fill's minute on give the last price, result, R, MAE and MFE")
  func marks() throws {
    let bars = [
      try bar(0, low: "9.00", high: "12.00", close: "9.50"),
      try bar(1, low: "9.80", high: "10.20", close: "10.10"),
      try bar(2, low: "9.70", high: "10.60", close: "10.40"),
    ]
    let marks = try #require(PositionMarks(bars: bars, entry: 10, stop: Decimal(string: "9.50"), quantity: 100,
      openedAt: try time(1, second: 30)))
    #expect(marks.last == Decimal(string: "10.40"))
    #expect(marks.unrealized == 40)
    #expect(marks.resultR == Decimal(string: "0.8"))
    #expect(marks.adverse == Decimal(string: "-0.30"))
    #expect(marks.favorable == Decimal(string: "0.60"))
    #expect(marks.adverseR == Decimal(string: "-0.6"))
    #expect(marks.favorableR == Decimal(string: "1.2"))
    // The line starts at the entry fill, then one close per bar since the open.
    #expect(marks.closes == [10, Decimal(string: "10.10")!, Decimal(string: "10.40")!])
  }

  @Test("Marks are current only while the newest bar closed within the stale limit")
  func freshness() throws {
    let bars = [try bar(2, low: "9.70", high: "10.60", close: "10.40")]
    let marks = try #require(PositionMarks(bars: bars, entry: 10, stop: 9, quantity: 1, openedAt: try time(2)))
    // The bar 15:02 closes at 15:03:00; the next one, 15:03, cannot be captured before 15:04:00.
    #expect(marks.isFresh(now: try time(4, second: 30), staleSeconds: 30))
    #expect(!marks.isFresh(now: try time(4, second: 31), staleSeconds: 30))
  }

  @Test("No R without a stop below the entry; no marks without a bar since the open")
  func undefined() throws {
    let bars = [try bar(0, low: "10.50", high: "11.00", close: "10.80")]
    let marks = try #require(PositionMarks(bars: bars, entry: 10, stop: nil, quantity: 1, openedAt: try time(0)))
    #expect(marks.resultR == nil)
    #expect(marks.adverse == 0)
    // One bar after the fill already draws a line: from the entry to its close.
    #expect(marks.closes == [10, Decimal(string: "10.80")!])
    #expect(PositionMarks(bars: bars, entry: 10, stop: 9, quantity: 1, openedAt: try time(5)) == nil)
  }

  @Test("The live bid replaces the last close: result, R and extremes follow it, the line ends at it")
  func live() throws {
    let bars = [try bar(1, low: "9.80", high: "10.20", close: "10.10")]
    let marks = try #require(PositionMarks(bars: bars, entry: 10, stop: Decimal(string: "9.50"), quantity: 100,
      openedAt: try time(1)))
    #expect(!marks.isLive)
    let position = try fixturePosition()
    let price = try #require(live(position))
    #expect(price.bid == Decimal(string: "12.50"))
    let now = marks.live(price)
    #expect(now.isLive)
    #expect(now.last == price.bid)
    #expect(now.lastAt == price.quotedAt)
    #expect(now.unrealized == 250)
    #expect(now.resultR == 5)
    #expect(now.high == price.bid)
    #expect(now.favorable == Decimal(string: "2.50"))
    #expect(now.closes == marks.closes + [price.bid])
    // Live marks are current whatever the bars' age.
    #expect(now.isFresh(now: try time(30), staleSeconds: 0))
    #expect(marks.live(nil) == marks)
  }

  @Test("No live price from a stale status, a lost or absent stream, or a position without a quote")
  func noLivePrice() throws {
    let position = try fixturePosition()
    #expect(live(position, stream: .quiet) != nil)
    #expect(live(position, statusStale: true) == nil)
    #expect(live(position, stream: .lost) == nil)
    #expect(live(position, stream: .off) == nil)
    // A quote older than the limit is not live, however healthy the stream; at the limit it is.
    let quoted = try #require(position.quote).quotedAt.date
    #expect(live(position, ageSeconds: 60) != nil)
    #expect(live(position, ageSeconds: 61) == nil)
    // No limit from an older API: no quote is live.
    #expect(LivePrice(position: position, stream: .fresh, statusStale: false, now: quoted, maxAgeSeconds: nil) == nil)
    // A bot older than the key writes no quote: the position decodes without one.
    var json = try #require(try JSONSerialization.jsonObject(with: fixture("status-response-v1.json")) as? [String: Any])
    var status = try #require(json["status"] as? [String: Any])
    var first = try #require(status["position"] as? [String: Any])
    first.removeValue(forKey: "quote")
    status["position"] = first
    json["status"] = status
    let older = try Wire.decoder().decode(StatusResponseV1.self, from: JSONSerialization.data(withJSONObject: json))
    let without = try #require(older.status?.position)
    #expect(without.quote == nil)
    #expect(live(without) == nil)
  }

  /// The live price of `position` `ageSeconds` after its quote, with a 60 s limit.
  func live(_ position: Position, stream: StreamHealth = .fresh, statusStale: Bool = false,
    ageSeconds: TimeInterval = 1) -> LivePrice? {
    let quoted = position.quote?.quotedAt.date ?? Date()
    return LivePrice(position: position, stream: stream, statusStale: statusStale,
      now: quoted.addingTimeInterval(ageSeconds), maxAgeSeconds: 60)
  }

  /// The first position of the shared status fixture (bid 12.50, ask 12.52).
  func fixturePosition() throws -> Position {
    let response = try Wire.decoder().decode(StatusResponseV1.self, from: fixture("status-response-v1.json"))
    return try #require(response.status?.position)
  }
}
