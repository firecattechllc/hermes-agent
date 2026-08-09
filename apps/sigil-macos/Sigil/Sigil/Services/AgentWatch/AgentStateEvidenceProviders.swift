import Foundation

/// The only lifecycle data Agent Watch accepts from agent integrations.
/// Payload content, commands, prompts, output, and environment data are not
/// represented and therefore cannot cross this boundary.
nonisolated enum SanitizedAgentLifecycleEvent: String, Codable, Sendable {
    case sessionStarted
    case beganWork
    case waitingForInput
    case approvalRequired
    case completed
    case failed
    case ended
}

nonisolated struct SanitizedAgentEvent: Codable, Equatable, Sendable {
    let agent: AgentKind
    let processID: Int32
    let event: SanitizedAgentLifecycleEvent
    let observedAt: Date
}

nonisolated protocol AgentStateEvidenceProviding: Sendable {
    func evidence(for session: AgentWatchSession, now: Date) -> AgentStateEvidence?
    func evidenceBackedSessions(now: Date) -> [AgentWatchSession]
}

nonisolated extension AgentStateEvidenceProviding {
    func evidenceBackedSessions(now: Date) -> [AgentWatchSession] { [] }
}

nonisolated struct LocalAgentStateEvidenceProvider: AgentStateEvidenceProviding {
    let directory: URL
    let freshnessInterval: TimeInterval

    nonisolated init(
        directory: URL = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appending(path: "SigilDev/AgentWatch/Events", directoryHint: .isDirectory),
        freshnessInterval: TimeInterval = 15 * 60
    ) {
        self.directory = directory
        self.freshnessInterval = freshnessInterval
    }

    nonisolated func evidence(for session: AgentWatchSession, now: Date) -> AgentStateEvidence? {
        let url = directory.appending(path: "\(session.kind.rawValue)-\(session.processID).json")
        guard let signal = validatedSignal(at: url, now: now),
              signal.agent == session.kind, signal.processID == session.processID
        else { return nil }
        return Self.map(signal.event)
    }

    nonisolated func evidenceBackedSessions(now: Date) -> [AgentWatchSession] {
        guard let urls = try? FileManager.default.contentsOfDirectory(
            at: directory, includingPropertiesForKeys: nil
        ) else { return [] }
        return urls.compactMap { url in
            guard url.lastPathComponent.hasPrefix("codex-"),
                  url.pathExtension == "json",
                  let signal = validatedSignal(at: url, now: now),
                  signal.agent == .codex,
                  url.deletingPathExtension().lastPathComponent == "codex-\(signal.processID)"
            else { return nil }
            let (state, reason) = AgentStateClassifier().classify(Self.map(signal.event))
            return AgentWatchSession(
                id: "codex:\(signal.processID):lifecycle",
                kind: .codex,
                displayName: AgentKind.codex.displayName,
                state: state,
                stateReason: reason,
                evidenceSource: .nativeLifecycle,
                evidenceConfidence: .high,
                processID: signal.processID,
                parentProcessID: nil,
                host: "This Mac",
                workingDirectory: nil,
                associatedApplication: nil,
                applicationBundleIdentifier: nil,
                startTime: signal.observedAt,
                lastActivityTime: signal.observedAt,
                lastStateChangeTime: now
            )
        }
    }

    private nonisolated func validatedSignal(at url: URL, now: Date) -> SanitizedAgentEvent? {
        guard let data = try? Data(contentsOf: url),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              Set(object.keys) == ["agent", "processID", "event", "observedAt"],
              let signal = try? JSONDecoder.agentWatch.decode(SanitizedAgentEvent.self, from: data),
              signal.processID > 1,
              signal.observedAt <= now,
              now.timeIntervalSince(signal.observedAt) <= (signal.agent == .codex ? 8 : freshnessInterval)
        else { return nil }
        return signal
    }

    nonisolated static func map(_ event: SanitizedAgentLifecycleEvent) -> AgentStateEvidence {
        switch event {
        case .sessionStarted:
            AgentStateEvidence(processAlive: true, idleReason: "Native session started")
        case .beganWork:
            AgentStateEvidence(processAlive: true, activelyExecuting: true)
        case .waitingForInput:
            AgentStateEvidence(processAlive: true, waitingReason: "Agent reported waiting for operator input")
        case .approvalRequired:
            AgentStateEvidence(processAlive: true, attentionReason: "Agent reported an approval request")
        case .completed:
            AgentStateEvidence(processAlive: true, completionReason: "Agent reported completion")
        case .failed:
            AgentStateEvidence(processAlive: true, failureReason: "Agent reported an unexpected failure")
        case .ended:
            AgentStateEvidence(processAlive: false, completionReason: "Native session ended")
        }
    }
}

nonisolated extension JSONDecoder {
    static var agentWatch: JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }
}

/// Claude Code hook mapping. Only hook names and notification categories are
/// consumed by the integration that writes `SanitizedAgentEvent` records.
nonisolated struct ClaudeCodeStateEvidenceProvider: Sendable {
    nonisolated func event(hookName: String, notificationType: String? = nil) -> SanitizedAgentLifecycleEvent? {
        switch (hookName, notificationType) {
        case ("SessionStart", _):
            .sessionStarted
        case ("PermissionRequest", _), ("Notification", "permission_prompt"):
            .approvalRequired
        case ("Notification", "idle_prompt"):
            .waitingForInput
        case ("PreToolUse", _), ("PostToolUse", _), ("PostToolUseFailure", _), ("UserPromptSubmit", _):
            .beganWork
        case ("Stop", _), ("TaskCompleted", _):
            .completed
        case ("SessionEnd", _):
            .ended
        default:
            nil
        }
    }
}

/// Codex native command-hook lifecycle mapping. Request bodies and turn
/// content are deliberately ignored; only the allowlisted event name is used.
nonisolated struct CodexStateEvidenceProvider: Sendable {
    nonisolated func event(method: String, blocking: Bool? = nil, terminalStatus: String? = nil) -> SanitizedAgentLifecycleEvent? {
        switch method {
        case "SessionStart": .sessionStarted
        case "UserPromptSubmit", "PreToolUse", "PostToolUse": .beganWork
        case "PermissionRequest": .approvalRequired
        case "Stop": .completed
        case "SessionEnd": .ended
        case "turn/started": .beganWork
        case "item/commandExecution/requestApproval", "item/fileChange/requestApproval", "item/permissions/requestApproval": .approvalRequired
        case "item/tool/requestUserInput" where blocking == true: .waitingForInput
        case "turn/completed" where terminalStatus == "completed": .completed
        case "turn/completed" where terminalStatus == "failed": .failed
        case "agent-turn-complete": .completed
        default: nil
        }
    }
}
