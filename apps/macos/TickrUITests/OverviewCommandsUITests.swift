import XCTest

/// The bot's commands on Overview in the narrowest window the app allows. The Safety · bot card
/// sits in a row of a fixed height (OverviewView.middleRowHeight), so every command must still
/// lie inside the card, be hittable, and (except pausing, which acts at once and is never
/// pressed here) open its confirmation. Run in English and in Russian, whose labels are the
/// longest; the macOS app has one text size (no Dynamic Type on the Mac, Tokens.Typography).
/// The app reads the contract fixtures; every confirmation is cancelled.
final class OverviewCommandsUITests: XCTestCase {
  /// Environment variable of the fixture API (PreviewAPI.fixturesKey in the app) and its value.
  static let fixturesKey = "TICKR_FIXTURES"
  static let fixturesOn = "1"
  /// Launch arguments of each language, without restoring windows saved by an earlier run.
  static let english = ["-AppleLanguages", "(en)", "-AppleLocale", "en_US", "-ApplePersistenceIgnoreState", "YES"]
  static let russian = ["-AppleLanguages", "(ru)", "-AppleLocale", "ru_RU", "-ApplePersistenceIgnoreState", "YES"]
  /// The Overview's sidebar row and screen (AppSection ids), and the Safety · bot card
  /// (SafetyCard.id).
  static let overviewRow = "sidebar-overview"
  static let overviewScreen = "screen-overview"
  static let safetyCard = "overview-safety"
  /// Command ids (BotAction.id) with the fixture's one open position, ABCD
  /// (status-response-v1.json). Pausing acts at once; the others ask first.
  static let pause = "pause"
  static let confirmed = ["cancel", "flatten-ABCD", "flatten-all", "stop", "emergency"]
  /// The narrowest window (TickrMacApp.minWidth), in points.
  static let minWidth: CGFloat = 1000
  /// Identifier of the standard Window › Zoom item (its action), the same in every language.
  static let zoomItem = "performZoom:"
  /// How far the window's right and bottom edges are dragged in, as a fraction of the window:
  /// past the least size, which stops them there.
  static let shrinkShare = 0.8
  /// Seconds the corner is held before the drag, and points of room kept under the card after it
  /// is scrolled into view.
  static let dragSeconds: TimeInterval = 0.5
  static let margin: CGFloat = 24
  /// How long an element may take to appear, and how long a moving element must keep still
  /// before it is read or clicked, polled every `settlePoll`, in seconds.
  static let timeout: TimeInterval = 10
  static let settlePoll: TimeInterval = 0.2
  static let settleQuiet: TimeInterval = 1

  override func setUpWithError() throws {
    continueAfterFailure = false
  }

  @MainActor
  func testEveryCommandFitsAndAsksInTheNarrowestWindowInEnglish() {
    check(Self.english)
  }

  @MainActor
  func testEveryCommandFitsAndAsksInTheNarrowestWindowInRussian() {
    check(Self.russian)
  }

  @MainActor
  private func check(_ arguments: [String]) {
    let app = XCUIApplication()
    app.launchArguments = arguments
    app.launchEnvironment[Self.fixturesKey] = Self.fixturesOn
    app.launch()
    let window = app.windows.firstMatch
    XCTAssertTrue(window.waitForExistence(timeout: Self.timeout), "no window after launch")
    // The first window can be larger than a small display (its edges off screen): Zoom fits it
    // to the screen, then its right and bottom edges are dragged in past the least size.
    app.menuBarItems.containing(.menuItem, identifier: Self.zoomItem).firstMatch.click()
    app.menuItems[Self.zoomItem].click()
    let right = window.coordinate(withNormalizedOffset: CGVector(dx: 1, dy: 0.5))
    right.press(forDuration: Self.dragSeconds,
      thenDragTo: right.withOffset(CGVector(dx: -window.frame.width * Self.shrinkShare, dy: 0)))
    let bottom = window.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 1)).withOffset(CGVector(dx: 0, dy: -1))
    bottom.press(forDuration: Self.dragSeconds,
      thenDragTo: bottom.withOffset(CGVector(dx: 0, dy: -window.frame.height * Self.shrinkShare)))
    XCTAssertEqual(window.frame.width, Self.minWidth, accuracy: 1, "the window is not at its least width")

    let row = app.descendants(matching: .any)[Self.overviewRow].firstMatch
    XCTAssertTrue(row.waitForExistence(timeout: Self.timeout), "sidebar row\n\(app.debugDescription)")
    row.click()
    let screen = app.descendants(matching: .any)[Self.overviewScreen].firstMatch
    XCTAssertTrue(screen.waitForExistence(timeout: Self.timeout), "overview screen")
    let card = app.descendants(matching: .any)[Self.safetyCard].firstMatch
    waitUntilStill(card, "safety card", in: app)
    // Bring the whole card into the window: scroll the screen until its foot is in view.
    let below = card.frame.maxY - window.frame.maxY + Self.margin
    if below > 0 {
      screen.scroll(byDeltaX: 0, deltaY: -below)
      waitUntilStill(card, "safety card", in: app)
    }
    XCTAssertLessThanOrEqual(card.frame.maxY, window.frame.maxY, "the card is still below the window")

    let pause = card.buttons[Self.pause].firstMatch
    XCTAssertTrue(pause.waitForExistence(timeout: Self.timeout), "pause\n\(app.debugDescription)")
    assertInside(pause, card, Self.pause)
    for id in Self.confirmed {
      let button = card.buttons[id].firstMatch
      XCTAssertTrue(button.waitForExistence(timeout: Self.timeout), "\(id)\n\(app.debugDescription)")
      assertInside(button, card, id)
      XCTAssertTrue(button.isEnabled, "\(id) is off")
      button.click()
      let sheet = app.sheets.firstMatch
      XCTAssertTrue(sheet.waitForExistence(timeout: Self.timeout), "\(id) asks for confirmation")
      // Escape is the confirmation's cancel in every language.
      app.typeKey(.escape, modifierFlags: [])
      XCTAssertTrue(sheet.waitForNonExistence(timeout: Self.timeout), "\(id): the confirmation stays open")
    }
  }

  /// The command lies wholly inside the card, so the card's fixed row cuts none of it off, and a
  /// click on it reaches it.
  @MainActor
  private func assertInside(_ button: XCUIElement, _ card: XCUIElement, _ id: String) {
    XCTAssertTrue(card.frame.contains(button.frame), "\(id) \(button.frame) runs out of the card \(card.frame)")
    XCTAssertTrue(button.isHittable, "\(id) is not hittable")
  }

  /// Waits until `element` exists and has kept the same frame for `settleQuiet` seconds.
  @MainActor
  private func waitUntilStill(_ element: XCUIElement, _ name: String, in app: XCUIApplication) {
    XCTAssertTrue(element.waitForExistence(timeout: Self.timeout), "\(name)\n\(app.debugDescription)")
    let deadline = Date.now.addingTimeInterval(Self.timeout)
    var frame = element.frame
    var stillSince = Date.now
    while Date.now < deadline {
      _ = XCTWaiter.wait(for: [XCTestExpectation(description: "settle")], timeout: Self.settlePoll)
      let now = element.frame
      if now != frame {
        frame = now
        stillSince = .now
      } else if Date.now.timeIntervalSince(stillSince) >= Self.settleQuiet {
        return
      }
    }
    XCTFail("\(name) kept moving\n\(app.debugDescription)")
  }
}
