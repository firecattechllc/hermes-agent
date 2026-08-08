import Foundation

nonisolated protocol AgentProcessAdapting: Sendable {
    func matches(_ process: AgentProcessSnapshot) -> Bool
    var kind: AgentKind { get }
}

nonisolated struct NamedAgentAdapter: AgentProcessAdapting {
    let kind: AgentKind
    let executableNames: Set<String>
    let pathFragments: [String]

    func matches(_ process: AgentProcessSnapshot) -> Bool {
        let name = process.executableName.lowercased()
        let path = process.executablePath.lowercased()
        return executableNames.contains(name) || pathFragments.contains(where: path.contains)
    }
}

nonisolated enum AgentAdapters {
    static let supported: [any AgentProcessAdapting] = [
        NamedAgentAdapter(kind: .claudeCode, executableNames: ["claude"], pathFragments: ["/@anthropic-ai/claude-code/", "/claude.app/"]),
        NamedAgentAdapter(kind: .codex, executableNames: ["codex", "codex-cli"], pathFragments: ["/@openai/codex/", "/codex.app/"]),
        NamedAgentAdapter(kind: .cursor, executableNames: ["cursor", "cursor helper"], pathFragments: ["/cursor.app/"]),
        NamedAgentAdapter(kind: .hermes, executableNames: ["hermes", "hermes-agent", "prime-agent"], pathFragments: ["/hermes-platform/", "/prime-agent/"])
    ]
}

nonisolated struct AgentStateEvidence: Equatable, Sendable {
    var processAlive: Bool
    var activelyExecuting: Bool = false
    var idleReason: String?
    var attentionReason: String?
    var waitingReason: String?
    var completionReason: String?
    var failureReason: String?
    var source: AgentEvidenceSource = .nativeLifecycle
    var confidence: AgentEvidenceConfidence = .high
}

nonisolated struct AgentStateClassifier: Sendable {
    func classify(_ evidence: AgentStateEvidence) -> (AgentWatchState, String) {
        if let reason = evidence.attentionReason { return (.needsAttention, reason) }
        if let reason = evidence.failureReason { return (.stuck, reason) }
        if let reason = evidence.completionReason { return (.done, reason) }
        if let reason = evidence.waitingReason { return (.waiting, reason) }
        if let reason = evidence.idleReason { return (.idle, reason) }
        if evidence.processAlive && evidence.activelyExecuting { return (.working, "Agent reports active work") }
        if evidence.processAlive { return (.unknown, "Process detected; no reliable activity signal") }
        return (.done, "Process exited")
    }
}

nonisolated struct AgentDiscoveryClassifier: Sendable {
    let adapters: [any AgentProcessAdapting]
    init(adapters: [any AgentProcessAdapting] = AgentAdapters.supported) { self.adapters = adapters }
    func kind(for process: AgentProcessSnapshot) -> AgentKind? { adapters.first(where: { $0.matches(process) })?.kind }
}
