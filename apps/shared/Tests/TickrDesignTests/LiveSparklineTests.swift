import Testing
@testable import TickrDesign

@Suite struct LiveSparklineTests {
  @Test("The line spans its values; a flat line sits mid-height")
  func span() {
    #expect(LiveSparkline.span([38.06, 38.4, 38.02]) == 38.02...38.4)
    let flat = LiveSparkline.span([12.5, 12.5])
    #expect(flat.lowerBound < 12.5 && flat.upperBound > 12.5)
    #expect(flat.upperBound - 12.5 == 12.5 - flat.lowerBound)
  }
}
