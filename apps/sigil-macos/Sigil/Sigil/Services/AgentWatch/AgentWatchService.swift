import AppKit
import Combine
import Foundation
import SwiftUI

@MainActor
final class AgentWatchService: ObservableObject {
    @Published private(set) var sessions: [AgentWatchSession] = []
    @Published private(set) var isKeepingAwake = false
    @AppStorage("agentWatch.keepAwakeEnabled") var keepAwakeEnabled = true

    private let discovery: AgentDiscoveryService
    private let notifications: AgentNotificationService
    private let power: any AgentPowerManaging
    private let evidenceProvider: any AgentStateEvidenceProviding
    private let lifecycleIntegration: AgentLifecycleIntegration
    private let thresholds: AgentWatchThresholds
    private var pollingTask: Task<Void, Never>?

    init(discovery: AgentDiscoveryService, notifications: AgentNotificationService, power: any AgentPowerManaging, evidenceProvider: any AgentStateEvidenceProviding, lifecycleIntegration: AgentLifecycleIntegration = AgentLifecycleIntegration(), thresholds: AgentWatchThresholds) {
        self.discovery = discovery; self.notifications = notifications; self.power = power; self.evidenceProvider = evidenceProvider; self.lifecycleIntegration = lifecycleIntegration; self.thresholds = thresholds
    }

    convenience init() {
        self.init(discovery: AgentDiscoveryService(), notifications: AgentNotificationService(), power: AgentPowerManager(), evidenceProvider: LocalAgentStateEvidenceProvider(), thresholds: .init())
    }

    var activeCount: Int { sessions.filter { $0.state == .working || $0.state == .waiting || $0.state == .needsAttention }.count }
    var attentionCount: Int { sessions.filter(\.requiresAttention).count }

    func start() {
        guard pollingTask == nil else { return }
        lifecycleIntegration.start()
        pollingTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.refresh()
                try? await Task.sleep(for: self?.thresholds.refreshInterval ?? .seconds(5))
            }
        }
    }

    func stop() { pollingTask?.cancel(); pollingTask = nil; lifecycleIntegration.stop(); power.releaseAssertion(); isKeepingAwake = false }

    func refresh(now: Date = .now) async {
        let discovered = await Task.detached { [discovery] in discovery.discover(now: now) }.value
        let previous = Dictionary(uniqueKeysWithValues: sessions.map { ($0.id, $0) })
        var nextSessions = discovered.map { fresh in
            var fresh = fresh
            if let evidence = evidenceProvider.evidence(for: fresh, now: now) {
                (fresh.state, fresh.stateReason) = AgentStateClassifier().classify(evidence)
                fresh.evidenceSource = evidence.source
                fresh.evidenceConfidence = evidence.confidence
                fresh.lastActivityTime = now
            }
            guard let old = previous[fresh.id] else { return fresh }
            var merged = fresh
            if fresh.state == old.state {
                merged.lastStateChangeTime = old.lastStateChangeTime
            }
            return merged
        }
        let nativeIdentities = Set(discovered.map { "\($0.kind.rawValue):\($0.processID)" })
        let evidenceSessions = evidenceProvider.evidenceBackedSessions(now: now).filter {
            !nativeIdentities.contains("\($0.kind.rawValue):\($0.processID)")
        }.map { fresh in
            guard let old = previous[fresh.id] else { return fresh }
            var merged = fresh
            if fresh.state == old.state { merged.lastStateChangeTime = old.lastStateChangeTime }
            return merged
        }
        nextSessions.append(contentsOf: evidenceSessions)
        let representedIdentities = Set(nextSessions.map { "\($0.kind.rawValue):\($0.processID)" })
        for var missing in previous.values where !representedIdentities.contains("\(missing.kind.rawValue):\(missing.processID)") {
            if let evidence = evidenceProvider.evidence(for: missing, now: now) {
                let priorState = missing.state
                (missing.state, missing.stateReason) = AgentStateClassifier().classify(evidence)
                missing.evidenceSource = evidence.source
                missing.evidenceConfidence = evidence.confidence
                if missing.state != priorState { missing.lastStateChangeTime = now }
                missing.lastActivityTime = now
            }
            guard missing.state == .done || missing.state == .stuck else { continue }
            let retentionSeconds = Double(thresholds.completedRetention.components.seconds)
            if now.timeIntervalSince(missing.lastStateChangeTime) <= retentionSeconds {
                nextSessions.append(missing)
            }
        }
        sessions = nextSessions.sorted { ($0.requiresAttention ? 0 : 1, $0.displayName) < ($1.requiresAttention ? 0 : 1, $1.displayName) }
        let awake = keepAwakeEnabled && sessions.contains(where: \.contributesToKeepAwake)
        power.update(shouldKeepAwake: awake); isKeepingAwake = power.isKeepingAwake
        await notifications.process(sessions)
    }

    deinit { pollingTask?.cancel() }
}
