import Foundation

/// Texts of the connection banner and empty state (ConnectionState) and the headline of a failed
/// read.
extension L10n {
  // MARK: Connection (ConnectionState)

  /// Title of the banner when nothing answers at the bot's address.
  static var connectionUnreachableTitle: LocalizedStringResource {
    LocalizedStringResource("kit.connection.unreachableTitle", defaultValue: "No connection to the bot", bundle: bundle)
  }
  /// Title of the banner when the bot's API answers with an error.
  static var connectionErrorTitle: LocalizedStringResource {
    LocalizedStringResource("kit.connection.errorTitle", defaultValue: "The bot's server answered with an error",
      bundle: bundle)
  }
  /// The banner's sentence over last known data; argument: the time of the last good read.
  static func connectionLastKnown(_ time: String) -> LocalizedStringResource {
    LocalizedStringResource("kit.connection.lastKnown",
      defaultValue: "Showing the last known data, from \(time). Commands are off until the connection is back.",
      bundle: bundle)
  }
  /// The empty state's sentence when nothing was ever read.
  static var connectionNothingYet: LocalizedStringResource {
    LocalizedStringResource("kit.connection.nothingYet",
      defaultValue: "Nothing received yet. Check that the Mac is awake and the bot is running. Commands are off.",
      bundle: bundle)
  }
  /// Title of the empty state before an address is set.
  static var connectionNotConfiguredTitle: LocalizedStringResource {
    LocalizedStringResource("kit.connection.notConfiguredTitle", defaultValue: "Connect to your Mac", bundle: bundle)
  }
  /// The empty state's sentence before an address is set.
  static var connectionNotConfiguredBody: LocalizedStringResource {
    LocalizedStringResource("kit.connection.notConfiguredBody",
      defaultValue: "Pair this device with the Mac in the connection settings.", bundle: bundle)
  }
  /// Title when the API answers but the bot has written no status.
  static var connectionNoBotStatusTitle: LocalizedStringResource {
    LocalizedStringResource("kit.connection.noBotStatusTitle", defaultValue: "The bot has not reported yet",
      bundle: bundle)
  }
  /// Sentence when the API answers but the bot has written no status.
  static var connectionNoBotStatusBody: LocalizedStringResource {
    LocalizedStringResource("kit.connection.noBotStatusBody",
      defaultValue: "The Mac answers, but the bot has no status to show. Entries are not possible.",
      bundle: bundle)
  }
  /// The banner's action that reads again now.
  static var connectionRetry: LocalizedStringResource {
    LocalizedStringResource("kit.connection.retry", defaultValue: "Retry", bundle: bundle)
  }
  /// The banner's action that opens the connection settings.
  static var connectionSettings: LocalizedStringResource {
    LocalizedStringResource("kit.connection.settings", defaultValue: "Connection settings", bundle: bundle)
  }
  /// The disclosure that shows the technical detail.
  static var connectionDetails: LocalizedStringResource {
    LocalizedStringResource("kit.connection.details", defaultValue: "Details", bundle: bundle)
  }
  /// The details' first line; argument: the address the app reads from.
  static func connectionAddress(_ address: String) -> LocalizedStringResource {
    LocalizedStringResource("kit.connection.address", defaultValue: "Address: \(address)", bundle: bundle)
  }
  /// Headline while the last read of the bot failed: plain words, the detail is in the banner.
  static var headlineNoConnection: LocalizedStringResource {
    LocalizedStringResource("kit.headline.noConnection", defaultValue: "No connection to the bot. Entry status unknown.",
      bundle: bundle)
  }
}
