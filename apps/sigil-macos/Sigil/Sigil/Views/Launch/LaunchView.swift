import SwiftUI

/// Wires exactly the four existing, already-governed, already-audited
/// paper-automation lifecycle commands (activate / deactivate / pause /
/// resume) — nothing else. Every action requires an explicit confirmation
/// dialog. There is no free-text input, no arbitrary command, and no path
/// to real broker submission anywhere in this screen: `broker_submission`
/// in the governed state always means the local paper simulation's own
/// flag, never a live brokerage connection.
struct LaunchView: View {
    @StateObject private var store = PaperExecutionStore()
    @State private var pendingAction: PendingAction?

    private enum PendingAction: Identifiable {
        case activate, deactivate, pause, resume
        var id: Self { self }

        var title: String {
            switch self {
            case .activate: return "Activate Paper Automation"
            case .deactivate: return "Deactivate Paper Automation"
            case .pause: return "Pause Paper Automation"
            case .resume: return "Resume Paper Automation"
            }
        }

        var message: String {
            switch self {
            case .activate: return "This starts the governed paper-only automation lifecycle when a governed backend is configured. No live broker submission will ever occur."
            case .deactivate: return "This deactivates configured paper automation and engages the kill switch."
            case .pause: return "This pauses configured paper automation."
            case .resume: return "This resumes previously-activated configured paper automation."
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
                                Text("Kill switch"); Spacer()
                                StatusBadge(state: status.killSwitch ? .connected : .degraded)
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
            Button(action.title, role: action == .deactivate ? .destructive : nil) {
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
        case .pause: await store.pause()
        case .resume: await store.resume()
        }
    }
}
