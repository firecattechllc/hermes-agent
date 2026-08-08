import SwiftUI

/// Native Settings: the one user-configurable value the governed backend
/// actually supports (Alpaca paper-trading API credentials, read via
/// `ALPACA_API_KEY`/`ALPACA_SECRET_KEY` by `_configured_credentials()`),
/// stored in Keychain only — never plaintext, never logged — plus
/// read-only diagnostic info. There is nothing here that can enable live
/// trading: Alpaca's paper endpoint is hardcoded in the governed backend
/// itself, not configurable from this screen.
struct SettingsView: View {
    @AppStorage("agentWatch.keepAwakeEnabled") private var keepAwakeEnabled = true
    private static let apiKeyAccount = "alpaca_api_key"
    private static let secretKeyAccount = "alpaca_secret_key"

    @State private var apiKeyInput = ""
    @State private var secretKeyInput = ""
    @State private var apiKeyStored = false
    @State private var secretKeyStored = false
    @State private var saveConfirmation: String?

    var body: some View {
        Form {
            Section("About") {
                LabeledContent("App", value: "Sigil 4.0 Mission Control")
                LabeledContent("Version", value: AppInfo.versionString)
                LabeledContent("Mode", value: "Paper Only")
                LabeledContent("Bundle Identifier", value: Bundle.main.bundleIdentifier ?? "unknown")
            }

            Section("Hermes Bridge") {
                LabeledContent("Endpoint", value: HermesBridgeClient.defaultBaseURLString)
                Text("Loopback-only. Never reachable from outside this Mac.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section("Agent Watch") {
                Toggle("Keep Mac available while agents work", isOn: $keepAwakeEnabled)
                Text("Prevents idle system sleep only while a session has reliable active-work evidence. The display may still sleep.")
                    .font(.caption).foregroundStyle(.secondary)
            }

            Section("Alpaca Paper Trading Credentials") {
                Text("Stored in this Mac's Keychain only — never written to a file, UserDefaults, or a log. Used solely by the governed backend's existing paper-execution reconciliation calls (Alpaca's paper endpoint, never live trading).")
                    .font(.caption)
                    .foregroundStyle(.secondary)

                SecureField(apiKeyStored ? "API key (stored — enter to replace)" : "API key", text: $apiKeyInput)
                SecureField(secretKeyStored ? "Secret key (stored — enter to replace)" : "Secret key", text: $secretKeyInput)

                HStack {
                    Button("Save to Keychain") { save() }
                        .disabled(apiKeyInput.isEmpty && secretKeyInput.isEmpty)
                    Button("Clear", role: .destructive) { clear() }
                        .disabled(!apiKeyStored && !secretKeyStored)
                    Spacer()
                    if apiKeyStored && secretKeyStored {
                        StatusBadge(state: .connected)
                    } else if apiKeyStored || secretKeyStored {
                        StatusBadge(state: .degraded)
                    } else {
                        StatusBadge(state: .unavailable)
                    }
                }

                if let saveConfirmation {
                    Text(saveConfirmation)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            Section("Safety") {
                LabeledContent("Live execution authority", value: "Never")
                LabeledContent("Broker submission", value: "Disabled unless governed by the paper runtime")
                LabeledContent("Hermes authority", value: "Authoritative")
            }
        }
        .formStyle(.grouped)
        .frame(maxWidth: 560)
        .onAppear(perform: refreshStoredState)
    }

    private func refreshStoredState() {
        apiKeyStored = KeychainStore.get(forAccount: Self.apiKeyAccount) != nil
        secretKeyStored = KeychainStore.get(forAccount: Self.secretKeyAccount) != nil
    }

    private func save() {
        if !apiKeyInput.isEmpty {
            KeychainStore.set(apiKeyInput, forAccount: Self.apiKeyAccount)
            apiKeyInput = ""
        }
        if !secretKeyInput.isEmpty {
            KeychainStore.set(secretKeyInput, forAccount: Self.secretKeyAccount)
            secretKeyInput = ""
        }
        refreshStoredState()
        saveConfirmation = "Saved to Keychain at \(Date().formatted(date: .omitted, time: .standard))."
    }

    private func clear() {
        KeychainStore.delete(forAccount: Self.apiKeyAccount)
        KeychainStore.delete(forAccount: Self.secretKeyAccount)
        refreshStoredState()
        saveConfirmation = "Cleared from Keychain."
    }
}
