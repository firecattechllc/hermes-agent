import Foundation

/// Everything Mission Control needs to render Overview.
///
/// This is the seam between the UI and whatever is actually supplying data.
/// `safety` is always enforced locally by this client, never derived from
/// the backend — Hermes remains authoritative, but the client's own refusal
/// to expose execution authority does not depend on the backend being
/// reachable. Every other field may come from the real governed backend
/// (`HermesBridgeDataProvider`) or, for sections not yet wired, from
/// `MockMissionControlDataProvider`.
struct MissionControlSnapshot {
    let safety: SafetyPosture

    /// Whether the local read-only Hermes bridge shim itself is reachable —
    /// a client-side diagnostic, distinct from any backend-reported health.
    let bridgeConnectivity: StatusEntry

    let runtimeHealth: StatusEntry
    let connectionState: StatusEntry
    let paperRuntimeState: StatusEntry
    let automationState: StatusEntry

    let hermesOrchestration: StatusEntry
    let modelRegistry: StatusEntry

    // Local AI Foundation (Mac Ollama) — kept as five distinct signals per
    // Phase 3A: service reachability, what's installed, what's actually
    // loaded in memory right now, whether the embedding model specifically
    // is available, and whether Sigil's own governed role admission
    // considers any of it healthy. These must never collapse into one badge.
    let ollamaService: StatusEntry
    let installedModels: StatusEntry
    let loadedModels: StatusEntry
    let embeddingModelAvailability: StatusEntry
    let providerHealth: StatusEntry

    // Governed fleet (Phase 3B) — authoritative source is the live Prime
    // server (`prime_fleet_status`), never a second, invented registry.
    // `fleetCertification` and `routing` intentionally stay separate from
    // per-node state: certification is fleet-wide, and routing/failover
    // comes from a different, structurally-unpopulated source
    // (`ai_status.fleet`) than node registration/health does.
    let fleetNodes: [FleetNode]
    let fleetCertification: StatusEntry
    let routing: StatusEntry

    let evidence: StatusEntry
    let latestResult: StatusEntry
}

/// Abstraction over "where Mission Control gets its data."
///
/// `MockMissionControlDataProvider` returns clearly labeled demo data.
/// `HermesBridgeDataProvider` reads real, read-only status from the governed
/// Hermes backend over the local bridge shim. Both conform to this same
/// protocol so views and stores never need to know which one is active.
protocol MissionControlDataProviding {
    func fetchSnapshot() async -> MissionControlSnapshot
}
