import Foundation

/// The live price of an open position: the bid of the newest quote the bot holds on its quote
/// stream, what the position would sell at. The bot writes it into every status (about every
/// three seconds), so it follows the market within seconds, where a captured one-minute bar is up
/// to two minutes behind. Display only; the broker's numbers stay the truth. Both apps show a
/// position's price and open result from it, and fall back to the captured bars only without it.
public struct LivePrice: Hashable, Sendable {
  /// The quote's bid.
  public let bid: Decimal
  /// The quote's ask, beside the bid where there is room.
  public let ask: Decimal
  /// When the provider quoted it.
  public let quotedAt: Date

  /// Nil when the status cannot be trusted as live (`statusStale`), the bot's stream is lost or
  /// off (its last quote may be far behind), or the bot holds no valid quote for the position.
  public init?(position: Position, stream: StreamHealth, statusStale: Bool) {
    guard !statusStale, stream == .fresh || stream == .quiet, let quote = position.quote else { return nil }
    bid = quote.bid.value
    ask = quote.ask.value
    quotedAt = quote.quotedAt.date
  }

  /// (bid − entry) × quantity, in dollars.
  public func unrealized(entry: Decimal, quantity: Decimal) -> Decimal { (bid - entry) * quantity }
}
