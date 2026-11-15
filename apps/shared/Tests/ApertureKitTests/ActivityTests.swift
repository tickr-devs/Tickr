@testable import ApertureKit
import Foundation
import Testing

@Suite struct ActivityTests {
  /// The fixture's status as JSON, changed by `edit`.
  func status(_ edit: (inout [String: Any]) -> Void = { _ in }) throws -> StatusV1 {
    var response = try #require(try JSONSerialization.jsonObject(with: fixture("status-response-v1.json")) as? [String: Any])
    var status = try #require(response["status"] as? [String: Any])
    edit(&status)
    response["status"] = status
    let decoded = try Wire.decoder().decode(StatusResponseV1.self, from: JSONSerialization.data(withJSONObject: response))
    return try #require(decoded.status)
  }

  @Test("A new write with nothing done is no event")
  func clockAloneIsNoEvent() throws {
    let later = try status {
      $0["written_at"] = "2026-11-16T15:00:02.123Z"
      $0["freshness"] = ["last_broker_read_at": "2026-11-16T15:00:02.123Z", "last_quote_at": NSNull(), "stream": "fresh"]
    }
    #expect(try status().activity == later.activity)
  }

  @Test("A closed position is an event")
  func positionClosedIsAnEvent() throws {
    let flat = try status { $0["positions"] = [Any]() }
    #expect(try status().activity != flat.activity)
  }
}
