import Testing
@testable import TickrDesign

@Suite struct SteadyFiguresTests {
  /// The longest shape among all of `texts` (a monospaced font draws it widest).
  static func longest(_ texts: [String]) -> Int {
    FigureShapes.shapes(texts).map(\.count).max() ?? 0
  }

  @Test func shapesWriteEveryDigitAsOneAndDropRepeats() {
    #expect(FigureShapes.shapes(["97.41", "97.52", "101.3K", "91.3K", "+0.08%", "\u{2212}0.07%"])
      == ["00.00", "000.0K", "00.0K", "+0.00%", "\u{2212}0.00%"])
  }

  /// The readout on the owner's screenshots: volume grew a digit and the change lost its plus.
  @Test func bucketsHoldTheWidestFigureOfEveryColumn() {
    let volumes: [Double] = [91_300, 101_300, 8_400, 1_250_000, 999_960, 640]
    let prices: [Double] = [97.41, 97.52, 0.9871, 1_002.5, 9.996, 12]
    let changes: [Double] = [0.0008, -0.0007, 0, -0.1234, 0.000_04]
    for (values, format) in [
      (volumes, Volume.compact),
      (prices, Money.price),
      (changes, { Percent.signed(fraction: $0) }),
    ] as [([Double], (Double) -> String)] {
      let all = values.map(format)
      #expect(Self.longest(FigureShapes.of(values, format)) == Self.longest(all))
    }
  }

  @Test func valuesThatAreNotFiniteAreLeftOut() {
    #expect(FigureShapes.of([Double.nan, .infinity], Money.price).isEmpty)
    #expect(FigureShapes.of([Double](), Money.price).isEmpty)
  }
}
