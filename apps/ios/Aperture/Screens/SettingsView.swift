import ApertureKit
import SwiftUI

/// Settings, behind the gear on Home: where the bot is, what the bot reports about itself, and
/// what this phone does. A standard grouped form, as iOS settings are.
struct SettingsView: View {
    @Environment(AppModel.self) private var model
    @State private var draft = ""
    @State private var showPairing = false
    @State private var confirmForget = false
    @FocusState private var editing: Bool

    var body: some View {
        Form {
            Section {
                TextField("http://127.0.0.1:8766", text: $draft)
                    .keyboardType(.URL)
                    .textContentType(.URL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .focused($editing)
                    .submitLabel(.done)
                    .onSubmit(save)
                    .font(.body.monospaced())
                if draft != model.connection.address {
                    Button("Save Address", action: save)
                        .disabled(!draft.isEmpty && Connection(address: draft).url == nil)
                }
                LabeledContent("Status") {
                    Text(connectionState.text).foregroundStyle(connectionState.color)
                }
                Button("Pair with Mac…") { showPairing = true }
                if model.connection.token != nil {
                    Button("Forget Pairing", role: .destructive) { confirmForget = true }
                }
            } header: {
                Text("Bot on your Mac")
            } footer: {
                Text("In the Simulator the Mac's own address works. On the phone, pair with the link that "
                    + "`python -m aperture.api pair` prints on the Mac; it reaches the Mac through Tailscale "
                    + "over HTTPS. The token stays in this phone's Keychain.")
            }

            if let status = model.status {
                Section("Bot") {
                    LabeledContent("State", value: status.botState.rawValue)
                    LabeledContent("Environment", value: status.environment)
                    LabeledContent("Trading gate") {
                        Text(status.tradingGate.open ? "open" : "closed")
                    }
                    if !status.tradingGate.open {
                        Text(status.tradingGate.cause).font(.footnote).foregroundStyle(.secondary)
                    }
                    LabeledContent("Strategy", value: "\(status.strategy.name) v\(status.strategy.version)")
                    if let runId = status.runId {
                        LabeledContent("Run", value: runId).font(.body.monospaced())
                    }
                }
            }

            Section {
                LabeledContent("Lock-screen alerts", value: "Not yet")
            } header: {
                Text("Notifications")
            } footer: {
                Text("Alerts for stops, fills, paused entries and broker mismatches arrive when push notifications "
                    + "are set up. Environment and limits are set in the bot's config on the Mac; the phone only shows them.")
            }

            Section("About") {
                LabeledContent("App", value: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "—")
                LabeledContent("API contract", value: "v\(Wire.apiVersion)")
            }
        }
        .navigationTitle("Settings")
        .onAppear { draft = model.connection.address }
        .onChange(of: model.connection.address) { _, address in draft = address }
        .sheet(isPresented: $showPairing) { PairingSheet() }
        .confirmationDialog("Forget the pairing?", isPresented: $confirmForget, titleVisibility: .visible) {
            Button("Forget Pairing", role: .destructive) {
                model.connection = Connection(address: model.connection.address, token: nil)
            }
        } message: {
            Text("The phone deletes its token and can no longer reach the bot remotely until you pair again.")
        }
    }

    /// Keeps the paired token only while the address still points at the same host.
    private func save() {
        let next = Connection(address: draft)
        guard draft.isEmpty || next.url != nil else { return }
        let sameHost = next.url?.host == model.connection.url?.host
        model.connection = Connection(address: draft, token: sameHost ? model.connection.token : nil)
        editing = false
    }

    private var connectionState: (text: String, color: Color) {
        if !model.isConfigured { return ("No address", .secondary) }
        if model.connection.isRemote && model.connection.token == nil { return ("Not paired", .orange) }
        if model.fetchError != nil { return ("Not reachable", .red) }
        if model.response == nil { return ("Connecting…", .secondary) }
        if model.status == nil || model.response?.stale == true { return ("Reached, status not current", .orange) }
        return ("Connected", .green)
    }
}

/// Paste the pairing link the Mac printed. Opening the link on the phone (AirDrop, Notes) works too.
private struct PairingSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var text = ""

    var body: some View {
        let pairing = Pairing.parse(text)
        NavigationStack {
            Form {
                Section {
                    TextField("aperture://pair?…", text: $text, axis: .vertical)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .font(.footnote.monospaced())
                        .lineLimit(3...6)
                    PasteButton(payloadType: String.self) { strings in
                        if let first = strings.first { text = first }
                    }
                } footer: {
                    Text("On the Mac run `python -m aperture.api --config config/bot.toml pair` and send the "
                        + "link to this phone, or paste it here.")
                }
                if let pairing {
                    Section("Mac") {
                        LabeledContent("Address", value: pairing.url.host ?? "")
                    }
                } else if !text.isEmpty {
                    Section {
                        Text("This is not a pairing link.").foregroundStyle(.red)
                    }
                }
            }
            .navigationTitle("Pair with Mac")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Pair") {
                        if let pairing { model.pair(pairing) }
                        dismiss()
                    }
                    .disabled(pairing == nil)
                }
            }
        }
    }
}
