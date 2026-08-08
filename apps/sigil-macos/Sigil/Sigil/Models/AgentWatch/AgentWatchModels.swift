import Foundation

nonisolated enum AgentWatchState: String, Codable, CaseIterable, Sendable {
    case working, needsAttention, waiting, stuck, done, idle, unknown

    nonisolated var label: String {
        switch self {
        case .needsAttention: "Needs You"
        default: rawValue.prefix(1).uppercased() + rawValue.dropFirst()
        }
    }
}

nonisolated enum AgentKind: String, Codable, CaseIterable, Sendable {
    case claudeCode, codex, cursor, hermes

    nonisolated var displayName: String {
        switch self {
        case .claudeCode: "Claude Code"
        case .codex: "Codex"
        case .cursor: "Cursor"
        case .hermes: "Hermes"
        }
    }

    nonisolated var systemImage: String {
        switch self {
        case .claudeCode: "brain.head.profile"
        case .codex: "terminal"
        case .cursor: "cursorarrow.rays"
        case .hermes: "bolt.horizontal.circle"
        }
    }
}

nonisolated enum AgentEvidenceSource: String, Codable, Sendable {
    case processDiscovery
    case nativeLifecycle
}

nonisolated enum AgentEvidenceConfidence: String, Codable, Sendable {
    case low
    case high
}

nonisolated struct AgentWatchSession: Identifiable, Equatable, Sendable {
    let id: String
    let kind: AgentKind
    let displayName: String
    var state: AgentWatchState
    var stateReason: String
    var evidenceSource: AgentEvidenceSource
    var evidenceConfidence: AgentEvidenceConfidence
    let processID: Int32
    let parentProcessID: Int32?
    let host: String
    let workingDirectory: String?
    let associatedApplication: String?
    let applicationBundleIdentifier: String?
    let startTime: Date?
    var lastActivityTime: Date?
    var lastStateChangeTime: Date
    var requiresAttention: Bool { state == .needsAttention }
    var contributesToKeepAwake: Bool { state == .working }
}

nonisolated struct AgentWatchThresholds: Equatable, Sendable {
    var refreshInterval: Duration = .seconds(5)
    var completedRetention: Duration = .seconds(30)
}

nonisolated struct AgentProcessSnapshot: Equatable, Sendable {
    let processID: Int32
    let parentProcessID: Int32?
    let executablePath: String
    let executableName: String
    let arguments: [String]
    let startTime: Date?
    let associatedApplication: String?
    let applicationBundleIdentifier: String?
}
