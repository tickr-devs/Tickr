import Foundation

/// Where the phone finds the bot's local API, and the paired token for a remote (HTTPS) address.
/// The address is in UserDefaults, the token in the Keychain; both stay on this phone. The token
/// belongs to the address it was paired with (`pairedAddress`) and is sent nowhere else.
struct Connection: Equatable {
    static let defaultsKey = "apiBaseURL"
    static let pairedKey = "apiPairedAddress"

    var address: String
    var token: String? = nil
    /// The exact address from the pairing link the token came with.
    var pairedAddress: String? = nil

    var isRemote: Bool { url?.scheme?.lowercased() == "https" }

    /// The token is sent only over HTTPS and only to the address it was paired with.
    var bearer: String? {
        guard isRemote, let token, let pairedAddress, let paired = Self.origin(pairedAddress),
              let current = url.flatMap({ Self.origin($0.absoluteString) }), paired == current
        else { return nil }
        return token
    }

    /// Scheme, host and port, lowercased; what decides where the token may go.
    static func origin(_ address: String) -> String? {
        guard let parts = URLComponents(string: address.trimmingCharacters(in: .whitespacesAndNewlines)),
              let scheme = parts.scheme?.lowercased(), let host = parts.host?.lowercased() else { return nil }
        return "\(scheme)://\(host)" + (parts.port.map { ":\($0)" } ?? "")
    }

    /// The address as a URL, or nil when it is empty or not an http(s) URL.
    var url: URL? {
        let trimmed = address.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let url = URL(string: trimmed), let scheme = url.scheme?.lowercased(),
              scheme == "http" || scheme == "https", url.host != nil else { return nil }
        return url
    }

    static func load(_ defaults: UserDefaults = .standard) -> Connection {
        if let saved = defaults.string(forKey: defaultsKey) {
            return Connection(address: saved, token: Keychain.readToken(),
                              pairedAddress: defaults.string(forKey: pairedKey))
        }
        #if targetEnvironment(simulator)
        // The Simulator shares the Mac's network, so the API's loopback address works as is.
        return Connection(address: "http://127.0.0.1:8766")
        #else
        return Connection(address: "")
        #endif
    }

    func save(_ defaults: UserDefaults = .standard) {
        defaults.set(address, forKey: Self.defaultsKey)
        if let token, let pairedAddress {
            Keychain.saveToken(token)
            defaults.set(pairedAddress, forKey: Self.pairedKey)
        } else {
            Keychain.deleteToken()
            defaults.removeObject(forKey: Self.pairedKey)
        }
    }
}
