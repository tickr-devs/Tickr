import Foundation

// Swift mirror of the decision makers' identities in src/tickr/api/contract.py (docs/api.md,
// "Decider"). Today every decision is a strategy's; `model` is sent only once the trained model decides.

/// A strategy's `[strategy]` name and version, as status-v1 records it.
public struct StrategyId: Codable, Hashable, Sendable {
  public let name: String
  public let version: String
}

/// What kind of decider made a decision.
public enum DeciderKind: String, Codable, Sendable { case strategy, model }

/// Who made a decision: a strategy (its `[strategy]` name and version) or a trained model bundle.
public struct DeciderV1: Codable, Hashable, Sendable {
  public let kind: DeciderKind
  public let name: String
  public let version: String
}
