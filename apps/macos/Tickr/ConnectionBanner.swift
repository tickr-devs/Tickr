import SwiftUI
import TickrDesign
import TickrKit

/// What every section says about the link to the bot (TickrKit `ConnectionState`), above the
/// section's screen: a banner over the last known data when a read fails, or over the section's own
/// empty cards when nothing was read yet; nothing while the link is fine. The technical cause
/// (address, port, the API's own words) is behind "Details"; commands stay off while it shows
/// (`commandsEnabled`).
struct ConnectionBanner: View {
  @Environment(AppModel.self) private var model
  /// A Retry is on its way: the button waits for it.
  @State private var retrying = false

  var body: some View {
    if let message = model.connectionMessage {
      let state = model.connectionState
      ConnectionNotice(.banner, title: message.title, message: message.body,
        tone: message.severity == .alarm ? .loss : .warning, systemImage: ConnectionNoticeLook.offlineSymbol,
        detailsLabel: ConnectionMessage.detailsLabel, details: details(message)) {
        Button {
          Task {
            retrying = true
            await model.refreshStatus()
            retrying = false
          }
        } label: { Text(ConnectionMessage.retry) }
          .buttonStyle(.action(state.hasNoData ? .primary : .secondary, size: .compact))
          .disabled(retrying)
        SettingsLink { Text(ConnectionMessage.settings) }
          .buttonStyle(.action(.ghost, size: .compact))
      }
      .padding([.horizontal, .top], Tokens.Space.l)
      .transition(.move(edge: .top).combined(with: .opacity))
    }
  }

  /// The technical lines under "Details": the address read from, then the failure as the API
  /// client describes it.
  private func details(_ message: ConnectionMessage) -> [String] {
    guard let detail = message.details else { return [] }
    return [String(localized: ConnectionMessage.address(model.baseURL.absoluteString)), detail]
  }
}
