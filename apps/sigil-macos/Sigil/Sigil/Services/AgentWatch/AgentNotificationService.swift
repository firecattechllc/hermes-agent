import UserNotifications

nonisolated protocol AgentNotificationDelivering: Sendable {
    func deliver(session: AgentWatchSession) async
}

nonisolated struct NativeAgentNotificationDelivery: AgentNotificationDelivering {
    nonisolated func deliver(session: AgentWatchSession) async {
        let center = UNUserNotificationCenter.current()
        _ = try? await center.requestAuthorization(options: [.alert, .sound])
        let content = UNMutableNotificationContent()
        content.title = "Agent Watch — \(session.state.label)"
        content.body = "\(session.displayName): \(session.stateReason)"
        content.userInfo = ["agentWatchSessionID": session.id]
        try? await center.add(UNNotificationRequest(identifier: "agent-watch.\(session.id).\(session.state.rawValue)", content: content, trigger: nil))
    }
}

actor AgentNotificationService {
    private let delivery: any AgentNotificationDelivering
    private var notifiedState: [String: AgentWatchState] = [:]
    init(delivery: any AgentNotificationDelivering = NativeAgentNotificationDelivery()) { self.delivery = delivery }

    func process(_ sessions: [AgentWatchSession]) async {
        let liveIDs = Set(sessions.map(\.id))
        notifiedState = notifiedState.filter { liveIDs.contains($0.key) }
        for session in sessions where session.state == .needsAttention || session.state == .stuck {
            guard notifiedState[session.id] != session.state else { continue }
            notifiedState[session.id] = session.state
            await delivery.deliver(session: session)
        }
        for session in sessions where session.state != .needsAttention && session.state != .stuck {
            notifiedState.removeValue(forKey: session.id)
        }
    }
}
