import Foundation

/// Where a price tag (the last price, an entry, a stop) sits on the price pane's scale: at its
/// price when the whole tag fits in the price area on screen, else pinned to the nearer edge of
/// that area, so a tag never hangs below the plot or over the volume bars. The tag still reads
/// its own price.
public enum PriceTagPlacement {
  /// The scale value to draw a tag for `price` at, given the prices from the bottom to the top
  /// of the price area (`prices`) and half a tag's height in price units (`halfHeight`). When a
  /// tag is taller than the whole area it sits in the area's middle.
  public static func position(of price: Double, in prices: ClosedRange<Double>, halfHeight: Double) -> Double {
    let lowest = prices.lowerBound + halfHeight
    let highest = prices.upperBound - halfHeight
    guard lowest <= highest else { return (prices.lowerBound + prices.upperBound) / 2 }
    return min(max(price, lowest), highest)
  }
}
