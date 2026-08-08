import Foundation
import Testing
@testable import SigilDev

struct AgentWatchTests {
    private func process(_ name: String, path: String? = nil, pid: Int32 = 42) -> AgentProcessSnapshot {
        AgentProcessSnapshot(processID: pid, parentProcessID: 1, executablePath: path ?? "/usr/local/bin/\(name)", executableName: name, arguments: [], startTime: Date(timeIntervalSince1970: 100), associatedApplication: nil, applicationBundleIdentifier: nil)
    }

    @Test func processClassification() {
        let classifier = AgentDiscoveryClassifier()
        #expect(classifier.kind(for: process("claude")) == .claudeCode)
        #expect(classifier.kind(for: process("codex")) == .codex)
        #expect(classifier.kind(for: process("Cursor", path: "/Applications/Cursor.app/Contents/MacOS/Cursor")) == .cursor)
        #expect(classifier.kind(for: process("hermes")) == .hermes)
        #expect(classifier.kind(for: process("unrelated")) == nil)
    }

    @Test func attentionHasPrecedence() {
        let result = AgentStateClassifier().classify(.init(processAlive: true, activelyExecuting: true, attentionReason: "Approval required", completionReason: "Finished"))
        #expect(result.0 == .needsAttention)
        #expect(result.1 == "Approval required")
    }

    @Test func evidenceBasedStates() {
        let classifier = AgentStateClassifier()
        #expect(classifier.classify(.init(processAlive: true, activelyExecuting: true)).0 == .working)
        #expect(classifier.classify(.init(processAlive: true)).0 == .unknown)
        #expect(classifier.classify(.init(processAlive: false)).0 == .done)
        #expect(classifier.classify(.init(processAlive: true, waitingReason: "Waiting for tool")).0 == .waiting)
        #expect(classifier.classify(.init(processAlive: true, failureReason: "Unexpected exit")).0 == .stuck)
    }

    @Test func multipleSessionsRemainIndependent() {
        let provider = StubProcessProvider(items: [process("claude", pid: 10), process("claude", pid: 11), process("codex", pid: 12), process("unknown", pid: 13)])
        let sessions = AgentDiscoveryService(provider: provider).discover(now: Date(timeIntervalSince1970: 200))
        #expect(sessions.count == 3)
        #expect(Set(sessions.map(\.id)).count == 3)
        #expect(sessions.allSatisfy { $0.state == .unknown })
    }

    @Test func claudeStructuredSignalsAreAllowlisted() {
        let provider = ClaudeCodeStateEvidenceProvider()
        #expect(provider.event(hookName: "PreToolUse") == .beganWork)
        #expect(provider.event(hookName: "PermissionRequest") == .approvalRequired)
        #expect(provider.event(hookName: "Notification", notificationType: "permission_prompt") == .approvalRequired)
        #expect(provider.event(hookName: "Notification", notificationType: "idle_prompt") == .waitingForInput)
        #expect(provider.event(hookName: "Stop") == .completed)
        #expect(provider.event(hookName: "PostToolUse") == nil)
    }

    @Test func codexStructuredSignalsAreAllowlisted() {
        let provider = CodexStateEvidenceProvider()
        #expect(provider.event(method: "turn/started") == .beganWork)
        #expect(provider.event(method: "item/commandExecution/requestApproval") == .approvalRequired)
        #expect(provider.event(method: "item/tool/requestUserInput", blocking: true) == .waitingForInput)
        #expect(provider.event(method: "turn/completed", terminalStatus: "completed") == .completed)
        #expect(provider.event(method: "turn/completed", terminalStatus: "failed") == .failed)
        #expect(provider.event(method: "item/agentMessage/delta") == nil)
    }

    @Test func evidenceFileRejectsUnknownFields() throws {
        let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString, directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let file = directory.appending(path: "claudeCode-10.json")
        try #"{"agent":"claudeCode","processID":10,"event":"beganWork","observedAt":"1970-01-01T00:03:20Z","prompt":"must reject"}"#.write(to: file, atomically: true, encoding: .utf8)
        let provider = LocalAgentStateEvidenceProvider(directory: directory, freshnessInterval: 900)
        #expect(provider.evidence(for: makeSession(state: .unknown), now: Date(timeIntervalSince1970: 200)) == nil)
    }

    @Test func staleSourceFallsBackToUnknownAndCanReconnect() throws {
        let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString, directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let file = directory.appending(path: "claudeCode-10.json")
        let provider = LocalAgentStateEvidenceProvider(directory: directory, freshnessInterval: 8)
        let session = makeSession(state: .unknown)

        try #"{"agent":"claudeCode","processID":10,"event":"beganWork","observedAt":"1970-01-01T00:03:20Z"}"#.write(to: file, atomically: true, encoding: .utf8)
        #expect(provider.evidence(for: session, now: Date(timeIntervalSince1970: 200))?.activelyExecuting == true)
        #expect(provider.evidence(for: session, now: Date(timeIntervalSince1970: 209)) == nil)

        try #"{"agent":"claudeCode","processID":10,"event":"completed","observedAt":"1970-01-01T00:03:29Z"}"#.write(to: file, atomically: true, encoding: .utf8)
        #expect(provider.evidence(for: session, now: Date(timeIntervalSince1970: 209))?.completionReason != nil)
    }

    @Test func notificationTransitionDeduplicatesAndCanReenter() async {
        let delivery = RecordingNotificationDelivery()
        let notifications = AgentNotificationService(delivery: delivery)
        let attention = makeSession(state: .needsAttention)
        await notifications.process([attention])
        await notifications.process([attention])
        #expect(await delivery.count == 1)
        await notifications.process([makeSession(state: .working)])
        await notifications.process([attention])
        #expect(await delivery.count == 2)
    }

    @Test @MainActor func keepAwakeAcquireHoldReleaseAndStop() async {
        let processProvider = MutableProcessProvider(items: [process("claude", pid: 10)])
        let power = RecordingPowerManager()
        let evidence = MutableEvidenceProvider(event: .beganWork)
        let service = AgentWatchService(
            discovery: AgentDiscoveryService(provider: processProvider),
            notifications: AgentNotificationService(delivery: RecordingNotificationDelivery()),
            power: power,
            evidenceProvider: evidence,
            thresholds: .init()
        )
        await service.refresh(now: Date(timeIntervalSince1970: 200))
        await service.refresh(now: Date(timeIntervalSince1970: 201))
        #expect(power.transitions == [true])
        evidence.event = .completed
        await service.refresh(now: Date(timeIntervalSince1970: 202))
        #expect(power.transitions == [true, false])
        evidence.event = .beganWork
        await service.refresh(now: Date(timeIntervalSince1970: 203))
        service.stop()
        #expect(power.transitions == [true, false, true, false])
    }

    /// Regression guard for the sidebar-navigation lifecycle bug: monitoring
    /// and the keep-awake assertion belong to the app/service layer
    /// (`RootView.task` starts once at launch; `SigilApp`'s
    /// `willTerminateNotification` handler stops once at quit) — never to
    /// whether the Agent Watch sidebar tab happens to be on screen.
    /// `AgentWatchView`/`AgentWatchMenuBarView` previously called
    /// `service.start()`/`service.stop()` from `onAppear`/`onDisappear`,
    /// which silently tore down background polling and released the power
    /// assertion the instant a user navigated to any other section. This
    /// test simulates the app remaining open and continuing to poll across
    /// many refresh cycles — standing in for the user navigating freely
    /// between sidebar sections while the service itself is never told to
    /// stop — and asserts monitoring and the assertion survive untouched
    /// until an explicit `stop()` (the sole app-termination-equivalent
    /// action) is called.
    @Test @MainActor func sidebarNavigationMustNotStopMonitoringOrReleaseAssertion() async {
        let processProvider = MutableProcessProvider(items: [process("claude", pid: 10)])
        let power = RecordingPowerManager()
        let evidence = MutableEvidenceProvider(event: .beganWork)
        let service = AgentWatchService(
            discovery: AgentDiscoveryService(provider: processProvider),
            notifications: AgentNotificationService(delivery: RecordingNotificationDelivery()),
            power: power,
            evidenceProvider: evidence,
            thresholds: .init()
        )

        await service.refresh(now: Date(timeIntervalSince1970: 200))
        #expect(power.transitions == [true])
        #expect(power.isKeepingAwake == true)
        #expect(service.isKeepingAwake == true)

        // Many further refresh cycles — the only thing that changes in the
        // background while a user freely opens/closes/switches away from the
        // Agent Watch tab. None of this may release the assertion or clear
        // sessions: the agent is still reporting `beganWork` the whole time.
        for tick in 1...20 {
            await service.refresh(now: Date(timeIntervalSince1970: 200 + Double(tick)))
            #expect(power.transitions == [true], "tick \(tick): navigation-equivalent activity must not touch the assertion")
            #expect(power.isKeepingAwake == true, "tick \(tick): keep-awake must still be held")
            #expect(!service.sessions.isEmpty, "tick \(tick): monitoring must still be observing the session")
        }

        // Only an explicit stop() — the app-termination-equivalent action —
        // may release the assertion.
        service.stop()
        #expect(power.transitions == [true, false])
        #expect(power.isKeepingAwake == false)
    }

    @Test @MainActor func terminalEvidenceSurvivesProcessDisappearanceWithinRetention() async {
        let processProvider = MutableProcessProvider(items: [process("claude", pid: 10)])
        let evidence = MutableEvidenceProvider(event: .beganWork)
        let service = AgentWatchService(
            discovery: AgentDiscoveryService(provider: processProvider),
            notifications: AgentNotificationService(delivery: RecordingNotificationDelivery()),
            power: RecordingPowerManager(),
            evidenceProvider: evidence,
            thresholds: AgentWatchThresholds(refreshInterval: .seconds(5), completedRetention: .seconds(30))
        )
        await service.refresh(now: Date(timeIntervalSince1970: 200))
        processProvider.items = []
        evidence.event = .completed
        await service.refresh(now: Date(timeIntervalSince1970: 201))
        #expect(service.sessions.count == 1)
        #expect(service.sessions.first?.state == .done)
        await service.refresh(now: Date(timeIntervalSince1970: 232))
        #expect(service.sessions.isEmpty)
    }

    private func makeSession(state: AgentWatchState) -> AgentWatchSession {
        AgentWatchSession(id: "claude:10:100", kind: .claudeCode, displayName: "Claude Code", state: state, stateReason: state.label, processID: 10, parentProcessID: 1, host: "This Mac", workingDirectory: nil, associatedApplication: nil, applicationBundleIdentifier: nil, startTime: Date(timeIntervalSince1970: 100), lastActivityTime: nil, lastStateChangeTime: Date(timeIntervalSince1970: 100))
    }
}

private struct StubProcessProvider: AgentProcessProviding {
    let items: [AgentProcessSnapshot]
    func snapshots() -> [AgentProcessSnapshot] { items }
}

private final class MutableProcessProvider: AgentProcessProviding, @unchecked Sendable {
    var items: [AgentProcessSnapshot]
    init(items: [AgentProcessSnapshot]) { self.items = items }
    nonisolated func snapshots() -> [AgentProcessSnapshot] { items }
}

private final class MutableEvidenceProvider: AgentStateEvidenceProviding, @unchecked Sendable {
    var event: SanitizedAgentLifecycleEvent
    init(event: SanitizedAgentLifecycleEvent) { self.event = event }
    nonisolated func evidence(for session: AgentWatchSession, now: Date) -> AgentStateEvidence? {
        LocalAgentStateEvidenceProvider.map(event)
    }
}

private actor RecordingNotificationDelivery: AgentNotificationDelivering {
    private(set) var count = 0
    func deliver(session: AgentWatchSession) { count += 1 }
}

@MainActor private final class RecordingPowerManager: AgentPowerManaging {
    private(set) var isKeepingAwake = false
    private(set) var transitions: [Bool] = []
    func update(shouldKeepAwake: Bool) {
        guard shouldKeepAwake != isKeepingAwake else { return }
        isKeepingAwake = shouldKeepAwake
        transitions.append(shouldKeepAwake)
    }
    func releaseAssertion() { update(shouldKeepAwake: false) }
}
