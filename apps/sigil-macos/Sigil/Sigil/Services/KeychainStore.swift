import Foundation
import Security

/// Minimal wrapper over macOS Keychain generic-password items, used only
/// for the Alpaca paper-trading API credentials the governed backend
/// already supports via `ALPACA_API_KEY`/`ALPACA_SECRET_KEY`
/// (`_configured_credentials()` in `apps/sigil/src/sigil/asset_catalog/catalog.py`).
///
/// Scoped under a service name unique to the Sigil 4.0 dev identity
/// (`com.firecattechnology.Sigil.dev`) so it can never collide with, read,
/// or overwrite anything Sigil 3.7 stores in the Keychain. Values are never
/// written to UserDefaults, a file, or any log — Keychain only.
enum KeychainStore {
    private static let service = "com.firecattechnology.Sigil.dev.credentials"

    static func set(_ value: String, forAccount account: String) {
        let data = Data(value.utf8)
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(query as CFDictionary)
        var attributes = query
        attributes[kSecValueData as String] = data
        attributes[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        SecItemAdd(attributes as CFDictionary, nil)
    }

    static func get(forAccount account: String) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var result: AnyObject?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        guard status == errSecSuccess, let data = result as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func delete(forAccount account: String) {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(query as CFDictionary)
    }
}
