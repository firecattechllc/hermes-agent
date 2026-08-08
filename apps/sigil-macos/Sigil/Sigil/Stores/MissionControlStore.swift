import Combine
import Foundation

/// Holds Mission Control's Overview state for the SwiftUI views.
///
/// This store is intentionally dumb: it asks a `MissionControlDataProviding`
/// for a snapshot and publishes it. It never contains trading logic, and it
/// never talks to a broker directly — Hermes remains authoritative. Network
/// I/O happens inside the provider (off the main actor, inside `URLSession`'s
/// own async machinery); this store only awaits the result and publishes it.
@MainActor
final class MissionControlStore: ObservableObject {
    @Published private(set) var snapshot: MissionControlSnapshot
    @Published private(set) var isLoading = false
    @Published private(set) var lastUpdatedAt: Date?

    /// Poll no faster than this for background auto-refresh. Manual refresh
    /// (the toolbar button) is not subject to this interval.
    private let pollInterval: Duration

    private let provider: MissionControlDataProviding

    init(
        provider: MissionControlDataProviding = HermesBridgeDataProvider(),
        pollInterval: Duration = .seconds(5)
    ) {
        self.provider = provider
        self.pollInterval = pollInterval
        // Seed synchronously so the first frame never shows an empty state.
        func loading(_ title: String) -> StatusEntry {
            StatusEntry(title: title, state: .mockData, detail: "Loading…")
        }
        self.snapshot = MissionControlSnapshot(
            safety: .current,
            bridgeConnectivity: loading("Hermes Bridge"),
            runtimeHealth: loading("Runtime Health"),
            connectionState: loading("Connection State"),
            paperRuntimeState: loading("Paper Runtime State"),
            automationState: loading("Automation State"),
            hermesOrchestration: loading("Hermes Orchestration"),
            modelRegistry: loading("Model Registry"),
            ollamaService: loading("Ollama Service"),
            installedModels: loading("Installed Models"),
            loadedModels: loading("Loaded Models"),
            embeddingModelAvailability: loading("Embedding Model"),
            providerHealth: loading("Provider Health"),
            fleetNodes: [],
            fleetCertification: loading("Fleet Certification"),
            routing: loading("Routing / Failover"),
            evidence: loading("Evidence / Artifacts"),
            latestResult: loading("Latest Result")
        )
    }

    /// One-shot refresh, used by both the manual Refresh button and each
    /// polling tick.
    func refresh() async {
        isLoading = true
        snapshot = await provider.fetchSnapshot()
        lastUpdatedAt = Date()
        isLoading = false
    }

    /// Runs until the enclosing task is cancelled (i.e. for as long as the
    /// view showing it is on screen). Call from a SwiftUI `.task` modifier,
    /// which cancels automatically when the view disappears.
    func pollContinuously() async {
        while !Task.isCancelled {
            await refresh()
            try? await Task.sleep(for: pollInterval)
        }
    }
}
