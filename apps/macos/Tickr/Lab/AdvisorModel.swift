import TickrKit
import Foundation
import Observation

/// The AI advisor's runs (GET /advisor) for the Lab's advisor card and page and the Overview's
/// advisor row, and the owner's Accept or Reject (POST /advisor/decision). A decision is a note:
/// the bot deploys nothing from it; running a version stays a config edit.
@MainActor
@Observable
final class AdvisorModel {
  /// Seconds between two reads while a screen that shows the advisor is open: runs come from the
  /// owner's own `python -m tickr.advisor`, a few a day at most.
  static let refreshSeconds: Double = 60

  /// The runs of the bot at `source`, once read.
  private(set) var runs: AdvisorRunsV1?
  /// Why they could not be read, if they could not.
  private(set) var failure: String?
  /// The run whose decision is on its way to the bot.
  private(set) var deciding: String?
  /// Why the last decision was not recorded (the run is gone, or takes no decision).
  private(set) var decisionFailure: String?
  /// The Lab shows the advisor page instead of its cards.
  var pageOpen = false
  /// The run the page shows; nil = the leading one.
  var selected: String?
  /// The bot the runs came from.
  @ObservationIgnored private var source: URL?

  /// The run the page shows.
  var shown: AdvisorRunV1? {
    selected.flatMap { id in runs?.runs.first { $0.id == id } } ?? runs?.lead
  }

  /// Open the page on `run` (the leading one when nil).
  func open(_ run: String? = nil) {
    selected = run
    decisionFailure = nil
    pageOpen = true
  }

  /// Read the runs of the bot at `url`; another bot's runs are dropped first.
  func load(_ url: URL) async {
    reset(for: url)
    do {
      let read = try await Container.shared.apiClient(APIClient.Endpoint(baseURL: url)).advisor()
      guard url == source else { return }
      runs = read
      failure = nil
    } catch is CancellationError {
      return
    } catch {
      guard url == source else { return }
      failure = AppModel.describe(error)
    }
  }

  /// Read the runs until cancelled; used while a screen that shows them is open.
  func follow(_ url: URL) async {
    while !Task.isCancelled {
      await load(url)
      try? await Task.sleep(for: .seconds(Self.refreshSeconds))
    }
  }

  /// Record the owner's verdict on `run` once; the answer is the runs as they now stand.
  func decide(_ url: URL, run: String, verdict: OwnerVerdict) async {
    reset(for: url)
    deciding = run
    defer { deciding = nil }
    do {
      let read = try await Container.shared.apiClient(APIClient.Endpoint(baseURL: url))
        .decideAdvisor(run: run, verdict: verdict)
      guard url == source else { return }
      runs = read
      decisionFailure = nil
    } catch APIError.refused(_, _, let cause) {
      guard url == source else { return }
      decisionFailure = cause
    } catch is CancellationError {
      return
    } catch {
      guard url == source else { return }
      decisionFailure = AppModel.describe(error)
    }
  }

  private func reset(for url: URL) {
    guard source != url else { return }
    runs = nil
    failure = nil
    decisionFailure = nil
    selected = nil
    source = url
  }
}
