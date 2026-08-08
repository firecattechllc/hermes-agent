import Combine
import Foundation

/// Backs the News screen. `status == "empty"` is a real, honest backend
/// state (nothing ingested yet in this isolated dev environment) — never
/// presented as an error or as fabricated content.
@MainActor
final class NewsStore: ObservableObject {
    @Published private(set) var status: GovernedNewsStatus?
    @Published private(set) var isLoading = false
    @Published private(set) var lastUpdatedAt: Date?
    @Published private(set) var errorMessage: String?

    private let client: HermesBridgeClient

    init(client: HermesBridgeClient = HermesBridgeClient()) {
        self.client = client
    }

    func refresh() async {
        isLoading = true
        errorMessage = nil
        do {
            status = try await client.governedNewsStatus()
            lastUpdatedAt = Date()
        } catch {
            errorMessage = "Hermes bridge unreachable or news store unavailable."
        }
        isLoading = false
    }
}
