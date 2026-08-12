import Foundation

/// Reads real, read-only status from the governed Hermes backend via the
/// embedded, loopback-only XPC bridge and maps it into `MissionControlSnapshot`.
///
/// Every field here is either genuinely live (state derived from the
/// backend's own semantic health values) or an honest `.unavailable` /
/// `.offline` when the backend doesn't expose it or the bridge can't be
/// reached — never `.mockData` (that label is reserved for values that were
/// never queried at all) and never `.connected` merely because a request
/// succeeded.
struct HermesBridgeDataProvider: MissionControlDataProviding {
    private let client: HermesBridgeClient

    nonisolated init(client: HermesBridgeClient = HermesBridgeClient()) {
        self.client = client
    }

    func fetchSnapshot() async -> MissionControlSnapshot {
        async let healthTask: BridgeHealthResult? = try? client.health()
        async let runtimeTask: RuntimeSnapshotResult? = try? client.runtimeSnapshot()
        async let aiTask: AIStatusResult? = try? client.aiStatus()
        async let primeTask: PrimeFleetStatusResult? = try? client.primeFleetStatus()

        let health = await healthTask
        let runtime = await runtimeTask
        let ai = await aiTask
        let prime = await primeTask

        return MissionControlSnapshot(
            safety: .current,
            bridgeConnectivity: Self.bridgeConnectivityEntry(health: health),
            runtimeHealth: Self.runtimeHealthEntry(runtime: runtime),
            connectionState: Self.connectionStateEntry(runtime: runtime),
            paperRuntimeState: Self.paperRuntimeStateEntry(runtime: runtime),
            automationState: Self.automationStateEntry(runtime: runtime),
            hermesOrchestration: Self.orchestrationEntry(ai: ai),
            modelRegistry: Self.modelRegistryEntry(ai: ai),
            ollamaService: Self.ollamaServiceEntry(ai: ai),
            installedModels: Self.installedModelsEntry(ai: ai),
            loadedModels: Self.loadedModelsEntry(ai: ai),
            embeddingModelAvailability: Self.embeddingModelEntry(ai: ai),
            providerHealth: Self.providerHealthEntry(ai: ai),
            fleetNodes: Self.fleetNodes(prime: prime),
            fleetCertification: Self.fleetCertificationEntry(prime: prime),
            routing: Self.routingEntry(ai: ai),
            evidence: Self.evidenceEntry(ai: ai),
            latestResult: Self.latestResultEntry(ai: ai, runtime: runtime)
        )
    }

    // MARK: - Semantic health mapping
    //
    // These translate the backend's own vocabulary into a coarse ServiceState
    // for the badge; the precise backend wording always still appears in the
    // row's detail text.

    static func state(forBackendHealth raw: String) -> ServiceState {
        switch raw {
        case "healthy":
            return .connected
        case "degraded", "configured_unverified", "recoverable_tail":
            return .degraded
        case "disabled":
            return .disabled
        case "empty":
            return .noData
        case "unconfigured", "not_configured":
            return .notConfigured
        case "not_ready":
            return .unavailable
        default:
            // "corrupt", "blocked", "identity_mismatch", "configuration_invalid",
            // "unavailable", and any future value we don't recognize yet.
            return .offline
        }
    }

    static func state(forConnection raw: String) -> ServiceState {
        switch raw {
        case "connected": return .connected
        case "degraded": return .degraded
        case "disconnected": return .offline
        case "not_configured": return .notConfigured
        default: return .unavailable
        }
    }

    private static func state(forOperationalState raw: String) -> ServiceState {
        switch raw {
        case "running": return .connected
        case "paused": return .degraded
        case "stopped": return .disabled
        case "blocked": return .offline
        default: return .unavailable
        }
    }

    // MARK: - Bridge connectivity (client-side diagnostic)

    private static func bridgeConnectivityEntry(health: BridgeHealthResult?) -> StatusEntry {
        guard let health else {
            return StatusEntry(
                title: "Hermes Bridge",
                state: .offline,
                detail: "Embedded bridge unavailable at \(HermesBridgeClient.defaultBaseURLString)."
            )
        }
        return StatusEntry(
            title: "Hermes Bridge",
            state: .connected,
            detail: "Reachable · bridge v\(health.bridgeVersion) · \(health.mode)"
        )
    }

    // MARK: - Runtime (from /runtime_snapshot)

    private static func runtimeHealthEntry(runtime: RuntimeSnapshotResult?) -> StatusEntry {
        guard let visibility = runtime?.runtimeVisibility else {
            return StatusEntry(title: "Runtime Health", state: .offline, detail: "Bridge unreachable — no runtime data.")
        }
        return StatusEntry(
            title: "Runtime Health",
            state: state(forBackendHealth: visibility.health),
            detail: visibility.health == "not_configured"
                ? "Bridge connected; governed paper-runtime backend not configured."
                : "Governed backend reports runtime health: \(visibility.health)"
        )
    }

    private static func connectionStateEntry(runtime: RuntimeSnapshotResult?) -> StatusEntry {
        guard let visibility = runtime?.runtimeVisibility else {
            return StatusEntry(title: "Connection State", state: .offline, detail: "Bridge unreachable — no connection data.")
        }
        return StatusEntry(
            title: "Connection State",
            state: state(forConnection: visibility.connectionState),
            detail: visibility.connectionState == "not_configured"
                ? "No governed backend connection is configured; the embedded bridge remains available."
                : "Backend connection state: \(visibility.connectionState)"
        )
    }

    private static func paperRuntimeStateEntry(runtime: RuntimeSnapshotResult?) -> StatusEntry {
        guard let visibility = runtime?.runtimeVisibility else {
            return StatusEntry(title: "Paper Runtime State", state: .offline, detail: "Bridge unreachable — no paper runtime data.")
        }
        return StatusEntry(
            title: "Paper Runtime State",
            state: visibility.paperExecutionAvailable ? .connected : (visibility.health == "not_configured" ? .notConfigured : .degraded),
            detail: visibility.paperExecutionAvailable
                ? "Paper execution available (paper-only; no broker submission)."
                : (visibility.health == "not_configured" ? "Paper runtime is optional and not configured on this Mac." : "Paper execution not currently available — see automation/authorization state.")
        )
    }

    private static func automationStateEntry(runtime: RuntimeSnapshotResult?) -> StatusEntry {
        guard let visibility = runtime?.runtimeVisibility else {
            return StatusEntry(title: "Automation State", state: .offline, detail: "Bridge unreachable — no automation data.")
        }
        let counts = visibility.counts
        return StatusEntry(
            title: "Automation State",
            state: state(forOperationalState: visibility.operationalState),
            detail: "\(visibility.operationalState) · mode: \(visibility.automationMode) · \(counts.cycles) cycles run"
        )
    }

    // MARK: - AI Foundation (from /ai_status)

    private static func orchestrationEntry(ai: AIStatusResult?) -> StatusEntry {
        guard let orchestration = ai?.orchestration else {
            return StatusEntry(title: "Hermes Orchestration", state: .offline, detail: "Bridge unreachable — no orchestration data.")
        }
        let orchestrationState: ServiceState = orchestration.enabled
            ? state(forBackendHealth: orchestration.health)
            : .disabled
        return StatusEntry(
            title: "Hermes Orchestration",
            state: orchestrationState,
            detail: orchestration.enabled
                ? "active: \(orchestration.activeCount) · completed: \(orchestration.completedCount) · failed: \(orchestration.failedCount) · paused: \(orchestration.pausedCount)"
                : "Optional orchestration is disabled by configuration."
        )
    }

    private static func modelRegistryEntry(ai: AIStatusResult?) -> StatusEntry {
        guard let ai else {
            return StatusEntry(title: "Model Registry", state: .offline, detail: "Bridge unreachable — no registry data.")
        }
        let registryState: ServiceState
        if ai.availableModelCount > 0 {
            registryState = .connected
        } else if ai.configuredModelCount > 0 {
            registryState = .degraded
        } else {
            registryState = .notConfigured
        }
        return StatusEntry(
            title: "Model Registry",
            state: registryState,
            detail: ai.configuredModelCount == 0
                ? "No model registry is configured (optional)."
                : "\(ai.availableModelCount)/\(ai.configuredModelCount) models available · revision: \(ai.registryRevision)"
        )
    }

    // MARK: - Local AI Foundation (Mac Ollama, from /ai_status.mac_ollama)
    //
    // Five deliberately separate signals — never collapsed into one badge:
    //   1. ollamaServiceEntry           — is the Ollama HTTP API reachable at all
    //   2. installedModelsEntry         — what Ollama has pulled locally
    //   3. loadedModelsEntry            — what's actually resident in memory right now
    //   4. embeddingModelEntry          — installed vs. loaded for the embedding model specifically
    //   5. providerHealthEntry          — Sigil's own governed role-admission health
    //
    // `serviceReachable`/`installedModels`/`runningModels` are only present
    // when the backend's opt-in probe is enabled (Sigil 4.0 dev only); `nil`
    // means "not probed", which reads as "Unavailable", never "Offline".

    private static func modelSummaryList(_ models: [OllamaModelSummary]) -> String {
        models.isEmpty ? "none" : models.map(\.name).joined(separator: ", ")
    }

    private static func ollamaServiceEntry(ai: AIStatusResult?) -> StatusEntry {
        guard let macOllama = ai?.macOllama else {
            return StatusEntry(title: "Ollama Service", state: .offline, detail: "Bridge unreachable — no service data.")
        }
        guard let reachable = macOllama.serviceReachable else {
            return StatusEntry(title: "Ollama Service", state: .optional, detail: "Optional local AI is disabled; service probe was not requested.")
        }
        return StatusEntry(
            title: "Ollama Service",
            state: reachable ? .connected : .offline,
            detail: reachable ? "Ollama HTTP API reachable on 127.0.0.1." : "Ollama HTTP API is not reachable — is Ollama running?"
        )
    }

    private static func installedModelsEntry(ai: AIStatusResult?) -> StatusEntry {
        guard let macOllama = ai?.macOllama else {
            return StatusEntry(title: "Installed Models", state: .offline, detail: "Bridge unreachable — no model data.")
        }
        guard let reachable = macOllama.serviceReachable, let installed = macOllama.installedModels else {
            return StatusEntry(title: "Installed Models", state: .optional, detail: "Optional local AI is disabled; installed models were not queried.")
        }
        guard reachable else {
            return StatusEntry(title: "Installed Models", state: .offline, detail: "Ollama service unreachable.")
        }
        return StatusEntry(
            title: "Installed Models",
            state: installed.isEmpty ? .noData : .connected,
            detail: installed.isEmpty ? "No models installed in Ollama." : "\(installed.count) installed: \(modelSummaryList(installed))"
        )
    }

    private static func loadedModelsEntry(ai: AIStatusResult?) -> StatusEntry {
        guard let macOllama = ai?.macOllama else {
            return StatusEntry(title: "Loaded Models", state: .offline, detail: "Bridge unreachable — no model data.")
        }
        guard let reachable = macOllama.serviceReachable, let running = macOllama.runningModels else {
            return StatusEntry(title: "Loaded Models", state: .optional, detail: "Optional local AI is disabled; loaded models were not queried.")
        }
        guard reachable else {
            return StatusEntry(title: "Loaded Models", state: .offline, detail: "Ollama service unreachable.")
        }
        return StatusEntry(
            title: "Loaded Models",
            state: running.isEmpty ? .disabled : .connected,
            detail: running.isEmpty ? "No models currently loaded in memory (Ollama unloads idle models)." : "\(running.count) loaded: \(modelSummaryList(running))"
        )
    }

    private static func embeddingModelEntry(ai: AIStatusResult?) -> StatusEntry {
        guard let macOllama = ai?.macOllama else {
            return StatusEntry(title: "Embedding Model", state: .offline, detail: "Bridge unreachable — no model data.")
        }
        guard let reachable = macOllama.serviceReachable,
              let installed = macOllama.installedModels,
              let embeddingRole = macOllama.roles["embedding"] else {
            return StatusEntry(title: "Embedding Model", state: .optional, detail: "Optional local AI is disabled; no embedding role is configured.")
        }
        let targetModel = embeddingRole.modelIdentity
        guard reachable else {
            return StatusEntry(title: "Embedding Model", state: .offline, detail: "Ollama unreachable — cannot check \(targetModel).")
        }
        let isInstalled = installed.contains { $0.name == targetModel }
        let isLoaded = macOllama.runningModels?.contains { $0.name == targetModel } ?? false
        let entryState: ServiceState = isLoaded ? .connected : (isInstalled ? .degraded : .unavailable)
        let detail: String
        if isLoaded {
            detail = "\(targetModel) is installed and currently loaded."
        } else if isInstalled {
            detail = "\(targetModel) is installed but not currently loaded in memory."
        } else {
            detail = "\(targetModel) is not installed in Ollama."
        }
        return StatusEntry(title: "Embedding Model", state: entryState, detail: detail)
    }

    private static func providerHealthEntry(ai: AIStatusResult?) -> StatusEntry {
        guard let macOllama = ai?.macOllama else {
            return StatusEntry(title: "Provider Health", state: .offline, detail: "Bridge unreachable — no provider data.")
        }
        guard macOllama.enabled else {
            return StatusEntry(
                title: "Provider Health",
                state: .disabled,
                detail: "Optional Mac Ollama role admission is disabled by configuration."
            )
        }
        let roleSummaries = macOllama.roles
            .sorted { $0.key < $1.key }
            .map { "\($0.key): \($0.value.health)" }
            .joined(separator: " · ")
        let anyHealthy = macOllama.roles.values.contains { $0.health == "healthy" }
        let anyConfigured = macOllama.roles.values.contains { $0.configured }
        let rolesState: ServiceState = anyHealthy ? .connected : (anyConfigured ? .degraded : .disabled)
        return StatusEntry(
            title: "Provider Health",
            state: rolesState,
            detail: roleSummaries.isEmpty ? "No roles admitted." : roleSummaries
        )
    }

    // MARK: - Governed fleet (Phase 3B, from /prime_fleet_status)
    //
    // Prime is the real, currently-relevant fleet membership/connectivity
    // system in this deployment shape. `ai_status.fleet` (used above through
    // Phase 3A) is real code but has no production writer anywhere in this
    // codebase, so it is kept only for Routing/Failover below, where "no
    // data recorded" legitimately reads as "None" rather than "Unavailable."
    //
    // Three per-node signals, always kept separate:
    //   - registration: is this node known to Prime at all
    //   - health: Prime's own connection_state for it (never inferred)
    //   - eligibility: a conservative, transparent read of connection_state
    //     — "connected" only. This is presentation, not a second registry:
    //     it derives from the one authoritative field Prime already reports.

    private static func state(forConnectionState raw: String) -> ServiceState {
        switch raw {
        case "connected": return .connected
        case "degraded", "stale": return .degraded
        case "disconnected": return .offline
        case "revoked": return .disabled
        default: return .unavailable // "unknown" — registered, never heartbeated; must not read as healthy
        }
    }

    private static func fleetNodes(prime: PrimeFleetStatusResult?) -> [FleetNode] {
        let roleInfo: [(name: String, role: String, key: String)] = [
            ("Titan", "Compute node", "titan"),
            ("Mac", "Local governed node", "mac"),
            ("Prime", "Governed routing node", "prime")
        ]

        guard let prime else {
            let unreachable = StatusEntry(title: "Registration", state: .offline, detail: "Bridge unreachable.")
            return roleInfo.map {
                FleetNode(
                    name: $0.name, role: $0.role,
                    registration: unreachable,
                    health: StatusEntry(title: "Health", state: .offline, detail: "Bridge unreachable."),
                    eligibility: StatusEntry(title: "Eligible for AI work", state: .offline, detail: "Bridge unreachable."),
                    capabilitiesText: "Unavailable.",
                    modelInventoryText: "Unavailable."
                )
            }
        }

        guard prime.configured else {
            let notConfigured = StatusEntry(title: "Registration", state: .notConfigured, detail: "Optional Prime fleet connection is not configured on this Mac (base URL and authentication token are missing).")
            return roleInfo.map {
                FleetNode(
                    name: $0.name, role: $0.role,
                    registration: notConfigured,
                    health: StatusEntry(title: "Health", state: .notConfigured, detail: "Not checked — Prime is not configured."),
                    eligibility: StatusEntry(title: "Eligible for AI work", state: .notConfigured, detail: "Not evaluated — Prime is not configured."),
                    capabilitiesText: "Not queried — Prime is not configured.",
                    modelInventoryText: "Not exposed by backend."
                )
            }
        }

        guard prime.reachable else {
            let unreachable = StatusEntry(title: "Registration", state: .offline, detail: "Prime is configured but unreachable.")
            return roleInfo.map {
                FleetNode(
                    name: $0.name, role: $0.role,
                    registration: unreachable,
                    health: StatusEntry(title: "Health", state: .offline, detail: "Prime configured but unreachable."),
                    eligibility: StatusEntry(title: "Eligible for AI work", state: .offline, detail: "Prime configured but unreachable."),
                    capabilitiesText: "Unavailable — Prime unreachable.",
                    modelInventoryText: "Not exposed by backend."
                )
            }
        }

        return roleInfo.map { info in
            guard let node = prime.nodes.first(where: { $0.naturalKey.lowercased() == info.key }) else {
                // Prime itself never appears in its own `/v1/fleet/nodes` list — it's
                // the hub, not a worker that heartbeats in. Its own reachability
                // (already proven true at this point) IS its honest health signal;
                // "not registered" would be a misleading thing to say about the hub.
                if info.key == "prime" {
                    let hub = StatusEntry(title: "Registration", state: .connected, detail: "Prime is the fleet hub itself — not a worker node.")
                    return FleetNode(
                        name: info.name, role: info.role,
                        registration: hub,
                        health: StatusEntry(title: "Health", state: .connected, detail: "Reachable and responding (this call itself succeeded)."),
                        eligibility: StatusEntry(title: "Eligible for AI work", state: .unavailable, detail: "Not applicable — Prime routes work, it does not perform it."),
                        capabilitiesText: "Fleet hub / routing authority.",
                        modelInventoryText: "Not applicable — Prime is not a worker node."
                    )
                }
                let notRegistered = StatusEntry(title: "Registration", state: .unavailable, detail: "Not registered with Prime.")
                return FleetNode(
                    name: info.name, role: info.role,
                    registration: notRegistered,
                    health: StatusEntry(title: "Health", state: .unavailable, detail: "Not registered."),
                    eligibility: StatusEntry(title: "Eligible for AI work", state: .unavailable, detail: "Not registered."),
                    capabilitiesText: "Not registered.",
                    modelInventoryText: "Not exposed by backend."
                )
            }
            let healthState = state(forConnectionState: node.connectionState)
            let eligible = node.connectionState == "connected"
            let inventory = node.modelInventory
            return FleetNode(
                name: info.name,
                role: info.role,
                registration: StatusEntry(title: "Registration", state: .connected, detail: "Registered with Prime as \(node.role)."),
                health: StatusEntry(title: "Health", state: healthState, detail: "connection_state: \(node.connectionState)"),
                eligibility: StatusEntry(
                    title: "Eligible for AI work",
                    state: eligible ? .connected : .unavailable,
                    detail: eligible ? "Eligible (connected)." : "Not eligible — requires connection_state == connected."
                ),
                capabilitiesText: node.capabilities.map { $0.isEmpty ? "none declared" : $0.joined(separator: ", ") } ?? "Not exposed by this endpoint.",
                modelInventoryText: inventory.map { $0.isEmpty ? "none reported" : $0.joined(separator: ", ") } ?? "Not exposed by this endpoint."
            )
        }
    }

    private static func fleetCertificationEntry(prime: PrimeFleetStatusResult?) -> StatusEntry {
        guard let prime else {
            return StatusEntry(title: "Fleet Certification", state: .offline, detail: "Bridge unreachable.")
        }
        guard prime.configured else {
            return StatusEntry(title: "Fleet Certification", state: .notConfigured, detail: "Not evaluated — optional Prime fleet connection is not configured.")
        }
        guard prime.reachable else {
            return StatusEntry(title: "Fleet Certification", state: .offline, detail: "Prime configured but unreachable.")
        }
        let cert = prime.certification
        let certState: ServiceState = cert.status == "certified" ? .connected : (cert.status == "unknown" ? .unavailable : .degraded)
        return StatusEntry(
            title: "Fleet Certification",
            state: certState,
            detail: cert.evidenceRef.map { "\(cert.status) · evidence: \($0)" } ?? cert.status
        )
    }

    // MARK: - Routing / Failover (from /ai_status.fleet — see note above)

    private static func routingEntry(ai: AIStatusResult?) -> StatusEntry {
        guard let fleet = ai?.fleet else {
            return StatusEntry(title: "Routing / Failover", state: .offline, detail: "Bridge unreachable — no routing data.")
        }
        let routingState: ServiceState
        if fleet.healthyNodeCount > 0 {
            routingState = .connected
        } else if fleet.registeredNodeCount > 0 {
            routingState = .degraded
        } else {
            routingState = .noData
        }
        let routeText = fleet.latestRoute.map { "\($0.nodeId) (\($0.state))" } ?? "None"
        let failoverText = fleet.latestFailover.map { "\($0.nodeId)" } ?? "None"
        return StatusEntry(
            title: "Routing / Failover",
            state: routingState,
            detail: fleet.registeredNodeCount == 0
                ? "No fleet routing data yet; optional fleet is not configured."
                : "Current route: \(routeText) · Fallback: \(failoverText) · \(fleet.healthyNodeCount)/\(fleet.registeredNodeCount) nodes healthy · \(fleet.completionUnknownTasks) tasks completion-unknown"
        )
    }

    private static func evidenceEntry(ai: AIStatusResult?) -> StatusEntry {
        guard let ai else {
            return StatusEntry(title: "Evidence / Artifacts", state: .offline, detail: "Bridge unreachable — no evidence data.")
        }
        let ledgerState = state(forBackendHealth: ai.evidenceLedgerHealth)
        let artifactState = state(forBackendHealth: ai.artifactStoreHealth)
        let combinedState = combinedEvidenceState(ledgerState: ledgerState, artifactState: artifactState)
        return StatusEntry(
            title: "Evidence / Artifacts",
            state: combinedState,
            detail: "\(ai.evidenceRecordCount) evidence records (\(ai.evidenceLedgerHealth)) · \(ai.artifactCount) artifacts (\(ai.artifactStoreHealth))"
        )
    }

    static func combinedEvidenceState(ledgerState: ServiceState, artifactState: ServiceState) -> ServiceState {
        if ledgerState == .offline || artifactState == .offline {
            return .offline
        } else if ledgerState == .degraded || artifactState == .degraded {
            return .degraded
        } else if ledgerState == .noData && artifactState == .noData {
            return .noData
        } else if ledgerState == .unavailable || artifactState == .unavailable {
            return .unavailable
        } else {
            return .connected
        }
    }

    private static func latestResultEntry(ai: AIStatusResult?, runtime: RuntimeSnapshotResult?) -> StatusEntry {
        guard ai != nil || runtime != nil else {
            return StatusEntry(title: "Latest Result", state: .offline, detail: "Bridge unreachable — no result data.")
        }
        let summary = ai?.latestAnalysisSummary
        let reconciliationCount = runtime?.runtimeVisibility.counts.reconciliation
        var detailParts: [String] = []
        detailParts.append(summary ?? "No analysis recorded yet")
        if let reconciliationCount {
            detailParts.append("\(reconciliationCount) reconciliation records")
        }
        return StatusEntry(
            title: "Latest Result",
            state: summary != nil ? .connected : .noData,
            detail: detailParts.joined(separator: " · ")
        )
    }
}
