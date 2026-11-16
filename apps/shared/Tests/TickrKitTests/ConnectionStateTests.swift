import Foundation
import Testing
@testable import TickrKit

@Suite("Connection state: what the screens say about the link to the bot")
struct ConnectionStateTests {
  let now = Date(timeIntervalSince1970: 1_800_000_000)
  let unreachable = ReadFailure(kind: .unreachable, detail: "Local API not reachable: Could not connect to the server.")

  func response(_ name: String) throws -> StatusResponseV1 {
    try Wire.decoder().decode(StatusResponseV1.self, from: fixture(name))
  }

  @Test("A link with no address is not configured, whatever was read")
  func notConfigured() throws {
    let state = ConnectionState.make(configured: false, response: try response("status-response-v1.json"),
      failure: unreachable, lastSuccess: now)
    #expect(state == .notConfigured)
    #expect(state.hasNoData)
  }

  @Test("Before the first answer the link is connecting and says nothing")
  func connecting() {
    let state = ConnectionState.make(configured: true, response: nil, failure: nil, lastSuccess: nil)
    #expect(state == .connecting)
    #expect(state.message(now: now) == nil)
  }

  @Test("An answered read is connected: no banner, data current")
  func connected() throws {
    let state = ConnectionState.make(configured: true, response: try response("status-response-v1.json"),
      failure: nil, lastSuccess: now)
    #expect(state == .connected)
    #expect(state.message(now: now) == nil)
    #expect(!state.isLastKnown)
  }

  @Test("A failed read after a good one keeps the last known data, marked as such")
  func staleAfterFailure() throws {
    let last = now.addingTimeInterval(-60)
    let state = ConnectionState.make(configured: true, response: try response("status-response-v1.json"),
      failure: unreachable, lastSuccess: last)
    #expect(state == .stale(lastSuccess: last, failure: unreachable))
    #expect(state.isLastKnown)
    #expect(!state.hasNoData)
    let message = try #require(state.message(now: now))
    #expect(message.title == L10n.connectionUnreachableTitle)
    #expect(message.body == L10n.connectionLastKnown(ConnectionState.time(last, now: now)))
    #expect(message.details == unreachable.detail)
    #expect(message.severity == .attention)
  }

  @Test("A failed read with nothing ever read is never connected: an empty state, an alarm")
  func neverConnected() {
    let state = ConnectionState.make(configured: true, response: nil, failure: unreachable, lastSuccess: nil)
    #expect(state == .neverConnected(failure: unreachable))
    #expect(state.hasNoData)
    #expect(!state.isLastKnown)
    #expect(state.message(now: now)?.severity == .alarm)
    #expect(state.message(now: now)?.body == L10n.connectionNothingYet)
  }

  @Test("An API that answers with an error is told apart from one that does not answer")
  func answeredWithError() {
    let failure = ReadFailure(kind: .answeredWithError, detail: "API refused (503 unavailable): demo day down")
    let state = ConnectionState.make(configured: true, response: nil, failure: failure, lastSuccess: nil)
    #expect(state.message(now: now)?.title == L10n.connectionErrorTitle)
  }

  @Test("The API answering without a bot status is its own state, its cause behind Details")
  func noBotStatus() throws {
    let state = ConnectionState.make(configured: true, response: try response("status-response-v1.unavailable.json"),
      failure: nil, lastSuccess: now)
    guard case .noBotStatus(let cause) = state else {
      Issue.record("expected noBotStatus, got \(state)")
      return
    }
    #expect(state.hasNoData)
    #expect(state.message(now: now)?.details == cause)
  }

  @Test("Failures are classified from the API client's error; a cancelled read is no failure")
  func failureFromError() {
    #expect(ReadFailure(APIError.unreachable("offline"))?.kind == .unreachable)
    #expect(ReadFailure(APIError.refused(status: 503, error: "unavailable", cause: "down"))?.kind == .answeredWithError)
    #expect(ReadFailure(APIError.contract("bad"))?.kind == .answeredWithError)
    #expect(ReadFailure(CancellationError()) == nil)
  }

  @Test("The last good read shows its clock time today, and its date before today")
  func lastSuccessTime() {
    let earlierToday = now.addingTimeInterval(-60)
    let yesterday = now.addingTimeInterval(-2 * 24 * 3600)
    #expect(ConnectionState.time(earlierToday, now: now) == earlierToday.formatted(date: .omitted, time: .shortened))
    #expect(ConnectionState.time(yesterday, now: now) == yesterday.formatted(date: .abbreviated, time: .shortened))
  }
}
