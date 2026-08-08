import Foundation

/// Demo-data provider used as a fallback / SwiftUI-preview source. All
/// values here are placeholders and are surfaced in the UI with an explicit
/// "Demo Data" label — never presented as a live connection. Real Overview
/// data comes from `HermesBridgeDataProvider` instead.
struct MockMissionControlDataProvider: MissionControlDataProviding {
    func fetchSnapshot() async -> MissionControlSnapshot {
        MissionControlSnapshot(
            safety: .current,
            bridgeConnectivity: StatusEntry(
                title: "Hermes Bridge",
                state: .mockData,
                detail: "Demo provider active; no bridge connection attempted."
            ),
            runtimeHealth: StatusEntry(
                title: "Runtime Health",
                state: .mockData,
                detail: "No live telemetry connection yet. Showing placeholder health state."
            ),
            connectionState: StatusEntry(
                title: "Connection State",
                state: .mockData,
                detail: "Not yet wired to this client."
            ),
            paperRuntimeState: StatusEntry(
                title: "Paper Runtime State",
                state: .mockData,
                detail: "Not yet wired to this client."
            ),
            automationState: StatusEntry(
                title: "Automation State",
                state: .mockData,
                detail: "Not yet wired to this client."
            ),
            hermesOrchestration: StatusEntry(
                title: "Hermes Orchestration",
                state: .mockData,
                detail: "Hermes governed orchestrator is not yet wired to this client."
            ),
            modelRegistry: StatusEntry(
                title: "Model Registry",
                state: .mockData,
                detail: "Not yet wired to this client."
            ),
            ollamaService: StatusEntry(
                title: "Ollama Service",
                state: .mockData,
                detail: "Not yet wired to this client."
            ),
            installedModels: StatusEntry(
                title: "Installed Models",
                state: .mockData,
                detail: "Not yet wired to this client."
            ),
            loadedModels: StatusEntry(
                title: "Loaded Models",
                state: .mockData,
                detail: "Not yet wired to this client."
            ),
            embeddingModelAvailability: StatusEntry(
                title: "Embedding Model",
                state: .mockData,
                detail: "Not yet wired to this client."
            ),
            providerHealth: StatusEntry(
                title: "Provider Health",
                state: .mockData,
                detail: "Not yet wired to this client."
            ),
            fleetNodes: ["Titan", "Mac", "Prime"].map { name in
                let placeholder = StatusEntry(title: name, state: .mockData, detail: "Not yet wired to this client.")
                return FleetNode(
                    name: name,
                    role: "Fleet node",
                    registration: placeholder,
                    health: placeholder,
                    eligibility: placeholder,
                    capabilitiesText: "Not yet wired to this client.",
                    modelInventoryText: "Not yet wired to this client."
                )
            },
            fleetCertification: StatusEntry(
                title: "Fleet Certification",
                state: .mockData,
                detail: "Not yet wired to this client."
            ),
            routing: StatusEntry(
                title: "Routing / Failover",
                state: .mockData,
                detail: "Routing and failover status is not yet available in this client."
            ),
            evidence: StatusEntry(
                title: "Evidence / Artifacts",
                state: .mockData,
                detail: "Evidence and artifact status is not yet available in this client."
            ),
            latestResult: StatusEntry(
                title: "Latest Result",
                state: .mockData,
                detail: "Not yet wired to this client."
            )
        )
    }
}
