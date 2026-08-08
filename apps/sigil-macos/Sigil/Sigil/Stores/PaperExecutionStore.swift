import Combine
import Foundation

/// Backs Portfolio, Executions, Reconciliation, and Launch — all four read
/// the same governed `paper_execution_status` projection, and Launch is the
/// only one that also triggers the four governed lifecycle actions. Every
/// action is paper-only; none of them ever sets real broker submission.
@MainActor
final class PaperExecutionStore: ObservableObject {
    @Published private(set) var status: PaperExecutionStatus?
    @Published private(set) var isLoading = false
    @Published private(set) var isPerformingAction = false
    @Published private(set) var lastUpdatedAt: Date?
    @Published private(set) var errorMessage: String?
    @Published private(set) var lastActionMessage: String?

    private let client: HermesBridgeClient

    init(client: HermesBridgeClient = HermesBridgeClient()) {
        self.client = client
    }

    func refresh() async {
        isLoading = true
        errorMessage = nil
        do {
            status = try await client.paperExecutionStatus()
            lastUpdatedAt = Date()
        } catch {
            errorMessage = Self.describe(error)
        }
        isLoading = false
    }

    func activate() async { await performAction("Activate") { try await $0.paperExecutionActivate() } }
    func deactivate() async { await performAction("Deactivate") { try await $0.paperExecutionDeactivate() } }
    func pause() async { await performAction("Pause") { try await $0.paperExecutionPause() } }
    func resume() async { await performAction("Resume") { try await $0.paperExecutionResume() } }

    private func performAction(
        _ label: String,
        _ action: @escaping (HermesBridgeClient) async throws -> PaperExecutionStatus
    ) async {
        isPerformingAction = true
        errorMessage = nil
        do {
            status = try await action(client)
            lastUpdatedAt = Date()
            lastActionMessage = "\(label) succeeded."
        } catch {
            lastActionMessage = nil
            errorMessage = "\(label) failed: \(Self.describe(error))"
        }
        isPerformingAction = false
    }

    private static func describe(_ error: Error) -> String {
        switch error {
        case HermesBridgeError.backendRejected(_, let message):
            return message.isEmpty ? "Backend rejected the request." : message
        case HermesBridgeError.transport:
            return "Hermes bridge unreachable."
        case HermesBridgeError.httpStatus(let code):
            return "Bridge returned HTTP \(code)."
        case HermesBridgeError.decoding:
            return "Could not parse the bridge response."
        default:
            return "\(error)"
        }
    }
}
