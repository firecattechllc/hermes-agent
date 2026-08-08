import Foundation

/// Parsed response types for the read-only Hermes bridge shim
/// (`apps/sigil/src/sigil/desktop_bridge/http_shim.py`), which is itself a
/// thin pass-through to the same governed Python backend the certified
/// Sigil 3.7 app already uses. These types intentionally model only the
/// fields Mission Control's Overview needs — not the full backend schema —
/// so this client stays a thin reader, not a shadow implementation.

/// Generic `{"ok": bool, "result": ..., "error": ..., "message": ...}` envelope.
struct BridgeEnvelope<Result: Decodable>: Decodable {
    let ok: Bool
    let result: Result?
    let error: String?
    let message: String?
}

// MARK: - /health

struct BridgeHealthResult: Decodable {
    let bridgeVersion: String
    let status: String
    let mode: String

    enum CodingKeys: String, CodingKey {
        case bridgeVersion = "bridge_version"
        case status
        case mode
    }
}

// MARK: - /runtime_snapshot

struct RuntimeSnapshotResult: Decodable {
    let runtimeVisibility: RuntimeVisibility

    enum CodingKeys: String, CodingKey {
        case runtimeVisibility = "runtime_visibility"
    }
}

struct RuntimeVisibility: Decodable {
    let operationalState: String
    let health: String
    let connectionState: String
    let automationMode: String
    let paperExecutionAvailable: Bool
    let counts: RuntimeCounts

    enum CodingKeys: String, CodingKey {
        case operationalState = "operational_state"
        case health
        case connectionState = "connection_state"
        case automationMode = "automation_mode"
        case paperExecutionAvailable = "paper_execution_available"
        case counts
    }
}

struct RuntimeCounts: Decodable {
    let cycles: Int
    let proposals: Int
    let executions: Int
    let reconciliation: Int
    let auditEvents: Int

    enum CodingKeys: String, CodingKey {
        case cycles, proposals, executions, reconciliation
        case auditEvents = "audit_events"
    }
}

// MARK: - /ai_status

struct AIStatusResult: Decodable {
    let enabled: Bool
    let serviceState: String
    let localGemmaHealth: String
    let configuredModelCount: Int
    let availableModelCount: Int
    let registryRevision: String
    let macOllama: MacOllamaStatus
    let evidenceRecordCount: Int
    let artifactCount: Int
    let evidenceLedgerHealth: String
    let artifactStoreHealth: String
    let latestAnalysisSummary: String?
    let orchestration: OrchestrationStatus
    let fleet: FleetStatus

    enum CodingKeys: String, CodingKey {
        case enabled
        case serviceState = "service_state"
        case localGemmaHealth = "local_gemma_health"
        case configuredModelCount = "configured_model_count"
        case availableModelCount = "available_model_count"
        case registryRevision = "registry_revision"
        case macOllama = "mac_ollama"
        case evidenceRecordCount = "evidence_record_count"
        case artifactCount = "artifact_count"
        case evidenceLedgerHealth = "evidence_ledger_health"
        case artifactStoreHealth = "artifact_store_health"
        case latestAnalysisSummary = "latest_analysis_summary"
        case orchestration
        case fleet
    }
}

struct MacOllamaStatus: Decodable {
    let enabled: Bool
    let roles: [String: OllamaRoleStatus]

    /// Present only when the backend's opt-in, read-only service probe is
    /// enabled (`SIGIL_AI_MAC_OLLAMA_PROBE_SERVICE=1`, set only for the
    /// Sigil 4.0 dev bridge shim — never for Sigil 3.7). `nil` means the
    /// probe wasn't run, not that Ollama is down; that distinction is
    /// preserved through to the UI as "not yet probed" rather than "offline".
    let serviceReachable: Bool?
    let installedModels: [OllamaModelSummary]?
    let runningModels: [OllamaModelSummary]?

    enum CodingKeys: String, CodingKey {
        case enabled, roles
        case serviceReachable = "service_reachable"
        case installedModels = "installed_models"
        case runningModels = "running_models"
    }
}

struct OllamaModelSummary: Decodable, Hashable {
    let name: String
    let size: Int?
    let digest: String?
}

struct OllamaRoleStatus: Decodable {
    let configured: Bool
    let health: String
    let modelIdentity: String
    let reason: String?

    enum CodingKeys: String, CodingKey {
        case configured, health, reason
        case modelIdentity = "model_identity"
    }
}

struct OrchestrationStatus: Decodable {
    let enabled: Bool
    let health: String
    let activeCount: Int
    let completedCount: Int
    let failedCount: Int
    let pausedCount: Int

    enum CodingKeys: String, CodingKey {
        case enabled, health
        case activeCount = "active_count"
        case completedCount = "completed_count"
        case failedCount = "failed_count"
        case pausedCount = "paused_count"
    }
}

struct FleetStatus: Decodable {
    let registeredNodeCount: Int
    let healthyNodeCount: Int
    let nodes: FleetNodeSet
    let latestRoute: FleetRoute?
    let latestFailover: FleetFailover?
    let activeTasks: Int
    let queuedTasks: Int
    let completionUnknownTasks: Int
    let clockWarnings: Int

    enum CodingKeys: String, CodingKey {
        case registeredNodeCount = "registered_node_count"
        case healthyNodeCount = "healthy_node_count"
        case nodes
        case latestRoute = "latest_route"
        case latestFailover = "latest_failover"
        case activeTasks = "active_tasks"
        case queuedTasks = "queued_tasks"
        case completionUnknownTasks = "completion_unknown_tasks"
        case clockWarnings = "clock_warnings"
    }
}

struct FleetNodeSet: Decodable {
    let titan: FleetNodeState?
    let mac: FleetNodeState?
    let prime: FleetNodeState?
}

struct FleetNodeState: Decodable {
    let nodeId: String
    let state: String
    let capabilities: [String]
    let load: Int

    enum CodingKeys: String, CodingKey {
        case nodeId = "node_id"
        case state, capabilities, load
    }
}

struct FleetRoute: Decodable {
    let nodeId: String
    let state: String

    enum CodingKeys: String, CodingKey {
        case nodeId = "node_id"
        case state
    }
}

struct FleetFailover: Decodable {
    let nodeId: String
    let failure: String?

    enum CodingKeys: String, CodingKey {
        case nodeId = "node_id"
        case failure
    }
}

// MARK: - /prime_fleet_status
//
// The real, currently-relevant fleet membership/connectivity system in this
// deployment shape — a live remote Prime HTTP server, reachable only if
// HERMES_PRIME_BASE_URL/HERMES_PRIME_AUTH_TOKEN are configured. Unlike
// `ai_status.fleet` (a heartbeat-based system with no production writer
// anywhere in this codebase), this reflects what Prime itself reports. If
// Prime isn't configured, `configured` is honestly false and `nodes` is
// empty — that must read as "Unavailable," never as "not registered."

struct PrimeFleetStatusResult: Decodable {
    let configured: Bool
    let reachable: Bool
    let baseURL: String?
    let nodes: [PrimeFleetNodeInfo]
    let certification: PrimeFleetCertification

    enum CodingKeys: String, CodingKey {
        case configured, reachable, nodes, certification
        case baseURL = "base_url"
    }
}

struct PrimeFleetNodeInfo: Decodable {
    let naturalKey: String
    let role: String
    let connectionState: String
    /// Only present if Prime's `/v1/fleet/nodes` response includes it for
    /// this node; absent (not empty) means "not exposed by this endpoint."
    let capabilities: [String]?
    /// Models this node has available, as last reported to Prime — this is
    /// an inventory, NOT a "currently selected for routing" signal (no such
    /// field exists anywhere reachable from Sigil; see HermesBridgeDataProvider).
    let modelInventory: [String]?

    enum CodingKeys: String, CodingKey {
        case naturalKey = "natural_key"
        case role
        case connectionState = "connection_state"
        case capabilities
        case modelInventory = "model_inventory"
    }
}

struct PrimeFleetCertification: Decodable {
    let status: String
    let evidenceRef: String?

    enum CodingKeys: String, CodingKey {
        case status
        case evidenceRef = "evidence_ref"
    }
}
