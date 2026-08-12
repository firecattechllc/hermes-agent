import SwiftUI

/// Wires the governed paper-automation lifecycle commands — activate /
/// deactivate / pause / resume, plus the two explicit safety controls this
/// screen adds, emergency stop and flatten positions — nothing else. Every
/// action requires an explicit confirmation dialog describing exactly what
/// it will and will not do. There is no free-text input, no arbitrary
/// command, and no path to real broker submission anywhere in this screen:
/// `broker_submission` in the governed state always means the local paper
/// simulation's own flag, never a live brokerage connection.
///
/// Emergency Stop and Flatten Positions are deliberately separate actions.
/// Stop prevents new exposure (blocks new orders, cancels resting/unfilled
/// ones); it never closes an already-filled position. Flatten is the only
/// action that ever closes positions, and it never fires automatically —
/// only this screen's own explicit, separately-confirmed button reaches it.
struct LaunchView: View {
    @StateObject private var store = PaperExecutionStore()
    @State private var pendingAction: PendingAction?

    private enum PendingAction: Identifiable {
        case activate, deactivate, pause, resume, emergencyStop, flattenPositions
        var id: Self { self }

        var title: String {
            switch self {
            case .activate: return "Activate Paper Automation"
            case .deactivate: return "Deactivate Paper Automation"
            case .pause: return "Pause Paper Automation"
            case .resume: return "Resume Paper Automation"
            case .emergencyStop: return "Emergency Stop"
            case .flattenPositions: return "Flatten All Positions"
            }
        }

        var message: String {
            switch self {
            case .activate: return "This starts the governed paper-only automation lifecycle when a governed backend is configured. No live broker submission will ever occur."
            case .deactivate: return "This deactivates configured paper automation and engages the kill switch."
            case .pause: return "This pauses configured paper automation."
            case .resume: return "This resumes previously-activated configured paper automation."
            case .emergencyStop:
                return "This immediately blocks all new paper order submission and attempts to cancel every resting/unfilled order. It does NOT close any already-filled position — use Flatten All Positions separately for that. This is paper-only and takes effect immediately; every step is audited, including any order it fails to cancel."
            case .flattenPositions:
                return "This closes EVERY currently-tracked paper position via the broker's close-position endpoint, one at a time. This is paper-only — it can never reach a live brokerage account. If any position fails to close, it will be reported explicitly here and in Audit; this action never reports success while a position remains open. This does not, by itself, stop new automation — use Emergency Stop separately for that."
            }
        }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                SafetyBannerView(safety: .current)

                SectionCard(title: "Automation State", systemImage: "paperplane") {
                    if let status = store.status {
                        VStack(alignment: .leading, spacing: 10) {
                            HStack {
                                Text("Activated"); Spacer()
                                StatusBadge(state: status.activated ? .connected : .disabled)
                            }
                            HStack {
                                Text("Paused"); Spacer()
                                StatusBadge(state: status.paused ? .degraded : .disabled)
                            }
                            HStack {
                                Text("Trading")
                                Spacer()
                                // Unambiguous by construction: this is plain
                                // text stating the actual meaning, not a
                                // reused connected/degraded badge a reader
                                // has to infer kill-switch semantics from.
                                Text(status.killSwitch ? "HALTED (kill switch engaged)" : "Armed")
                                    .font(.callout.weight(.semibold))
                                    .foregroundStyle(status.killSwitch ? .orange : .green)
                            }
                            if let message = store.lastActionMessage {
                                Text(message).font(.caption).foregroundStyle(.secondary)
                            }
                            if let error = store.errorMessage {
                                Text(error).font(.caption).foregroundStyle(.red)
                            }
                            if status.lifecycleActionsAvailable == false {
                                StatusRowView(entry: StatusEntry(
                                    title: "Governed Backend",
                                    state: .notConfigured,
                                    detail: status.lifecycleUnavailableReason ?? "Lifecycle actions are not available."
                                ))
                            }
                        }
                    } else {
                        ProgressView().padding()
                    }
                }

                SectionCard(title: "Governed Actions", systemImage: "hand.raised") {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("Every action below is paper-only, requires confirmation, and writes its own audit trail entry. This client never gains live execution or broker authority.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        HStack(spacing: 12) {
                            Button("Activate") { pendingAction = .activate }
                                .disabled(store.isPerformingAction || !(store.status?.lifecycleActionsAvailable ?? false) || (store.status?.activated ?? false))
                            Button("Pause") { pendingAction = .pause }
                                .disabled(store.isPerformingAction || !(store.status?.lifecycleActionsAvailable ?? false) || !(store.status?.activated ?? false) || (store.status?.paused ?? false))
                            Button("Resume") { pendingAction = .resume }
                                .disabled(store.isPerformingAction || !(store.status?.lifecycleActionsAvailable ?? false) || !(store.status?.paused ?? false))
                            Button("Deactivate", role: .destructive) { pendingAction = .deactivate }
                                .disabled(store.isPerformingAction || !(store.status?.lifecycleActionsAvailable ?? false) || !(store.status?.activated ?? false))
                            if store.isPerformingAction {
                                ProgressView().controlSize(.small)
                            }
                        }
                    }
                }

                SectionCard(title: "Safety Controls", systemImage: "exclamationmark.octagon") {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("Emergency Stop prevents new exposure (blocks new orders, cancels resting ones) — it never closes an existing position. Flatten All Positions is the separate, explicit action that closes positions. Neither ever triggers the other. Both are always available, paper-only, and fully audited, regardless of the automation state above.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        HStack(spacing: 12) {
                            Button("Emergency Stop", role: .destructive) { pendingAction = .emergencyStop }
                                .disabled(store.isPerformingAction)
                            Button("Flatten All Positions", role: .destructive) { pendingAction = .flattenPositions }
                                .disabled(store.isPerformingAction)
                            if store.isPerformingAction {
                                ProgressView().controlSize(.small)
                            }
                        }
                    }
                }
            }
            .padding(20)
            .frame(maxWidth: 900, alignment: .leading)
            .frame(maxWidth: .infinity)
        }
        .background(Color(nsColor: .windowBackgroundColor))
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button {
                    Task { await store.refresh() }
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
            }
        }
        .task { await store.refresh() }
        .confirmationDialog(
            pendingAction?.title ?? "",
            isPresented: Binding(get: { pendingAction != nil }, set: { if !$0 { pendingAction = nil } }),
            presenting: pendingAction
        ) { action in
            let destructive = action == .deactivate || action == .emergencyStop || action == .flattenPositions
            Button(action.title, role: destructive ? .destructive : nil) {
                Task { await perform(action) }
            }
            Button("Cancel", role: .cancel) {}
        } message: { action in
            Text(action.message)
        }
    }

    private func perform(_ action: PendingAction) async {
        switch action {
        case .activate: await store.activate()
        case .deactivate: await store.deactivate()
        case .emergencyStop: await store.emergencyStop()
        case .flattenPositions: await store.flattenPositions()
        case .pause: await store.pause()
        case .resume: await store.resume()
        }
    }
}
