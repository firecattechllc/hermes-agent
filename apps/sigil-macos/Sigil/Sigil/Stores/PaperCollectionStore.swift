import Combine
import Foundation

/// Reusable store for any `recent_*`/`paper_*` collection endpoint
/// (positions, orders, fills, proposals, candidates, rejections, audit).
/// Parameterized by which fetch call to make so each screen doesn't need
/// its own near-identical store.
@MainActor
final class PaperCollectionStore: ObservableObject {
    @Published private(set) var result: PaperCollectionResult?
    @Published private(set) var isLoading = false
    @Published private(set) var lastUpdatedAt: Date?
    @Published private(set) var errorMessage: String?

    private let fetch: () async throws -> PaperCollectionResult

    init(fetch: @escaping () async throws -> PaperCollectionResult) {
        self.fetch = fetch
    }

    func refresh() async {
        isLoading = true
        errorMessage = nil
        do {
            result = try await fetch()
            lastUpdatedAt = Date()
        } catch {
            errorMessage = Self.describe(error)
        }
        isLoading = false
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
