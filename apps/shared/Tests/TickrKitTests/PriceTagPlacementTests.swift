import Testing
@testable import TickrKit

@Suite("Where a price tag sits on the price scale")
struct PriceTagPlacementTests {
  /// A price area from 40 to 48 with tags half a dollar high on each side of their price.
  static let prices = 40.0...48.0
  static let half = 0.5

  @Test("A tag whose whole height fits stays at its price")
  func inside() {
    #expect(PriceTagPlacement.position(of: 44.2, in: Self.prices, halfHeight: Self.half) == 44.2)
    #expect(PriceTagPlacement.position(of: 40.5, in: Self.prices, halfHeight: Self.half) == 40.5)
    #expect(PriceTagPlacement.position(of: 47.5, in: Self.prices, halfHeight: Self.half) == 47.5)
  }

  @Test("A price below or above the area, or too near its edge, is pinned to that edge")
  func pinned() {
    #expect(PriceTagPlacement.position(of: 38.85, in: Self.prices, halfHeight: Self.half) == 40.5)
    #expect(PriceTagPlacement.position(of: 40.2, in: Self.prices, halfHeight: Self.half) == 40.5)
    #expect(PriceTagPlacement.position(of: 52, in: Self.prices, halfHeight: Self.half) == 47.5)
  }

  @Test("A tag taller than the whole area sits in its middle")
  func tooTall() {
    #expect(PriceTagPlacement.position(of: 38, in: 40.0...40.6, halfHeight: Self.half) == 40.3)
  }
}
