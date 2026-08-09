import Foundation

@objc protocol HermesBridgeServiceProtocol {
    func start(apiKey: String?, secretKey: String?, reply: @escaping (Bool, String?) -> Void)
    func stop(reply: @escaping () -> Void)
}

@MainActor
final class HermesBridgeServiceController {
    static let shared = HermesBridgeServiceController()

    private var connection: NSXPCConnection?
    private(set) var startupFailure: String?

    func start() {
        guard connection == nil else { return }
        guard let parentIdentifier = Bundle.main.bundleIdentifier else {
            startupFailure = "Sigil bundle identity unavailable"
            return
        }
        let connection = NSXPCConnection(serviceName: "\(parentIdentifier).HermesBridgeService")
        connection.remoteObjectInterface = NSXPCInterface(with: HermesBridgeServiceProtocol.self)
        connection.invalidationHandler = { [weak self] in
            Task { @MainActor in self?.connection = nil }
        }
        connection.interruptionHandler = { [weak self] in
            Task { @MainActor in self?.startupFailure = "Hermes bridge service interrupted" }
        }
        connection.resume()
        self.connection = connection

        let proxy = connection.remoteObjectProxyWithErrorHandler { [weak self] error in
            Task { @MainActor in self?.startupFailure = "Hermes bridge unavailable: \(error.localizedDescription)" }
        } as? HermesBridgeServiceProtocol
        proxy?.start(
            apiKey: KeychainStore.get(forAccount: "alpaca_api_key"),
            secretKey: KeychainStore.get(forAccount: "alpaca_secret_key")
        ) { [weak self] started, reason in
            Task { @MainActor in self?.startupFailure = started ? nil : (reason ?? "Hermes bridge failed to start") }
        }
    }

    func stop() {
        guard let connection else { return }
        let proxy = connection.remoteObjectProxy as? HermesBridgeServiceProtocol
        proxy?.stop {}
        connection.invalidate()
        self.connection = nil
    }
}
