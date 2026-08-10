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
    private static let primeBaseURLAccount = "prime_base_url"
    private static let primeAuthTokenAccount = "prime_auth_token"

    @State private var apiKeyInput = ""
    @State private var secretKeyInput = ""
    @State private var apiKeyStored = false
    @State private var secretKeyStored = false

    @State private var primeBaseURLInput = ""
    @State private var primeAuthTokenInput = ""
    @State private var primeBaseURLStored = false
    @State private var primeAuthTokenStored = false

    @State private var saveConfirmation: String?
    @State private var primeSaveConfirmation: String?

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

            Section("Optional System Configuration") {
                LabeledContent("Governed paper-runtime backend", value: "Not Configured")
                Text("The embedded bridge is healthy, but this production build has no configured governed backend connection. Lifecycle controls remain fail-closed.")
                    .font(.caption).foregroundStyle(.secondary)
                LabeledContent(
                    "Prime fleet",
                    value: primeBaseURLStored && primeAuthTokenStored ? "Configured" : "Not Configured"
                )

                TextField(
                    primeBaseURLStored ? "Prime base URL (stored — enter to replace)" : "Prime base URL",
                    text: $primeBaseURLInput
                )

                SecureField(
                    primeAuthTokenStored ? "Prime auth token (stored — enter to replace)" : "Prime auth token",
                    text: $primeAuthTokenInput
                )

                HStack {
                    Button("Save Prime Configuration") {
                        savePrimeConfiguration()
                    }
                    .disabled(primeBaseURLInput.isEmpty && primeAuthTokenInput.isEmpty)

                    Button("Clear Prime", role: .destructive) {
                        clearPrimeConfiguration()
                    }
                    .disabled(!primeBaseURLStored && !primeAuthTokenStored)

                    Spacer()

                    if primeBaseURLStored && primeAuthTokenStored {
                        StatusBadge(state: .connected)
                    } else if primeBaseURLStored || primeAuthTokenStored {
                        StatusBadge(state: .degraded)
                    } else {
                        StatusBadge(state: .unavailable)
                    }
                }

                if let primeSaveConfirmation {
                    Text(primeSaveConfirmation)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                Text("Stored only in this Mac's Keychain. The Prime authentication token is never displayed after saving.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                LabeledContent("Mac Ollama", value: "Optional · Disabled")
                Text("Requires an administrator-enabled local AI profile. No model or service is force-enabled by Sigil.")
                    .font(.caption).foregroundStyle(.secondary)
            }

            Section("Agent Watch") {
                Toggle("Keep Mac available while agents work", isOn: $keepAwakeEnabled)
                Text("Prevents idle system sleep only while a session has reliable active-work evidence. The display may still sleep.")
                    .font(.caption).foregroundStyle(.secondary)
            }

            Section("Alpaca Paper Trading Credentials") {
                Text("Stored in this Mac's Keychain only — never written to a file, UserDefaults, or a log. In this production build they enable read-only Alpaca IEX market snapshots; they do not configure a governed paper-runtime backend or enable broker submission.")
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
                LabeledContent("Broker submission", value: "Disabled")
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
        primeBaseURLStored = KeychainStore.get(forAccount: Self.primeBaseURLAccount) != nil
        primeAuthTokenStored = KeychainStore.get(forAccount: Self.primeAuthTokenAccount) != nil
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

    private func savePrimeConfiguration() {
        if !primeBaseURLInput.isEmpty {
            KeychainStore.set(primeBaseURLInput, forAccount: Self.primeBaseURLAccount)
            primeBaseURLInput = ""
        }

        if !primeAuthTokenInput.isEmpty {
            KeychainStore.set(primeAuthTokenInput, forAccount: Self.primeAuthTokenAccount)
            primeAuthTokenInput = ""
        }

        refreshStoredState()
        primeSaveConfirmation = "Prime configuration saved to Keychain."
    }

    private func clearPrimeConfiguration() {
        KeychainStore.delete(forAccount: Self.primeBaseURLAccount)
        KeychainStore.delete(forAccount: Self.primeAuthTokenAccount)

        primeBaseURLInput = ""
        primeAuthTokenInput = ""

        refreshStoredState()
        primeSaveConfirmation = "Prime configuration cleared from Keychain."
    }
}
