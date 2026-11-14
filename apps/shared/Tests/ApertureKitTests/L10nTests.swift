@testable import ApertureKit
import XCTest

/// The String Catalog ships in the package and answers in English and Russian.
final class L10nTests: XCTestCase {
  func text(_ resource: LocalizedStringResource, _ language: String) -> String {
    var resource = resource
    resource.locale = Locale(identifier: language)
    return String(localized: resource)
  }

  func testEnglishAndRussian() {
    XCTAssertEqual(text(L10n.commandFlatten, "en"), "Flatten")
    XCTAssertEqual(text(L10n.commandFlatten, "ru"), "Закрыть всё")
    XCTAssertEqual(text(L10n.tagEnter, "ru"), "ВХОД")
  }

  func testArgumentsFillTheTranslation() {
    XCTAssertEqual(text(L10n.apiRefused(409, "busy", "later"), "en"), "API refused (409 busy): later")
    XCTAssertEqual(text(L10n.apiRefused(409, "busy", "later"), "ru"), "API отказал (409 busy): later")
    let headline = text(L10n.headlineNoVerifiedStop("ABCD", "missing"), "ru")
    XCTAssertEqual(headline, "У позиции ABCD нет подтверждённого стопа (missing). ")
  }
}
