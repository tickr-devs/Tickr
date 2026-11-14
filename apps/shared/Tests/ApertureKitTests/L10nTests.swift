@testable import ApertureKit
import XCTest

/// The String Catalog ships in the package and answers in English and Russian.
final class L10nTests: XCTestCase {
  /// The resource in `language`, read from that language's folder of the package bundle (the
  /// test process itself runs in English).
  func text(_ resource: LocalizedStringResource, _ language: String) throws -> String {
    let folder = try XCTUnwrap(Bundle.module.url(forResource: language, withExtension: "lproj"),
      "no \(language).lproj in the package bundle")
    var resource = resource
    resource.locale = Locale(identifier: language)
    resource.bundle = .atURL(folder)
    return String(localized: resource)
  }

  func testEnglishAndRussian() throws {
    XCTAssertEqual(try text(L10n.commandFlatten, "en"), "Flatten")
    XCTAssertEqual(try text(L10n.commandFlatten, "ru"), "Закрыть всё")
    XCTAssertEqual(try text(L10n.tagEnter, "ru"), "ВХОД")
  }

  func testArgumentsFillTheTranslation() throws {
    XCTAssertEqual(try text(L10n.apiRefused(409, "busy", "later"), "en"), "API refused (409 busy): later")
    XCTAssertEqual(try text(L10n.apiRefused(409, "busy", "later"), "ru"), "API отказал (409 busy): later")
    let headline = try text(L10n.headlineNoVerifiedStop("ABCD", "missing"), "ru")
    XCTAssertEqual(headline, "У позиции ABCD нет подтверждённого стопа (missing). ")
  }
}
