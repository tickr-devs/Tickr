import Foundation

// Swift mirror of system-v1 `model` in src/tickr/api/contract.py (docs/api.md, "GET /system"):
// the trained model's switch, its configured bundle and its state where the API can know it.

/// The trained model's state: `off` while disabled; `notLoaded` and `refused` are reserved for when
/// the bot records its bundle loads.
public enum ModelState: String, Codable, Sendable {
  case off
  case notLoaded = "not_loaded"
  case refused
}

/// The trained model on the System card. `state` nil with `cause` when the API cannot know it.
public struct ModelV1: Codable, Hashable, Sendable {
  public let enabled: Bool
  public let bundle: String?
  public let state: ModelState?
  public let cause: String?

  enum CodingKeys: String, CodingKey { case enabled, bundle, state, cause }

  public init(from decoder: Decoder) throws {
    let c = try decoder.container(keyedBy: CodingKeys.self)
    enabled = try c.decode(Bool.self, forKey: .enabled)
    bundle = try c.decodeNullable(String.self, forKey: .bundle)
    state = try c.decodeNullable(ModelState.self, forKey: .state)
    cause = try c.decodeNullable(String.self, forKey: .cause)
  }

  public func encode(to encoder: Encoder) throws {
    var c = encoder.container(keyedBy: CodingKeys.self)
    try c.encode(enabled, forKey: .enabled)
    try c.encodeNullable(bundle, forKey: .bundle)
    try c.encodeNullable(state, forKey: .state)
    try c.encodeNullable(cause, forKey: .cause)
  }
}
