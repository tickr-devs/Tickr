import Foundation

/// Where the phone finds the bot's local API, and the paired token for a remote (HTTPS) address.
/// The address is in UserDefaults, the token in the Keychain; both stay on this phone.
struct Connection: Equatable {
    static let defaultsKey = "apiBaseURL"

    var address: String
    var token: String? = nil

    var isRemote: Bool { url?.scheme?.lowercased() == "https" }

    /// The token is sent only over HTTPS, never to a plain-HTTP address.
    var bearer: String? { isRemote ? token : nil }

    /// The address as a URL, or nil when it is empty or not an http(s) URL.
    var url: URL? {
        let trimmed = address.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let url = URL(string: trimmed), let scheme = url.scheme?.lowercased(),
              scheme == "http" || scheme == "https", url.host != nil else { return nil }
        return url
    }

    static func load(_ defaults: UserDefaults = .standard) -> Connection {
        if let saved = defaults.string(forKey: defaultsKey) {
            return Connection(address: saved, token: Keychain.readToken())
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
        if let token { Keychain.saveToken(token) } else { Keychain.deleteToken() }
    }
}
