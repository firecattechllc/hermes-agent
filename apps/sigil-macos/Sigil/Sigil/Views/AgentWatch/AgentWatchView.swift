import SwiftUI

struct AgentWatchStatusBadge: View {
    let state: AgentWatchState
    var body: some View {
        Text(state.label).font(.caption.weight(.semibold)).padding(.horizontal, 8).padding(.vertical, 3)
            .background(color.opacity(0.14), in: Capsule()).foregroundStyle(color)
    }
    private var color: Color {
        switch state { case .needsAttention, .stuck: .orange; case .working: .green; case .done: .blue; default: .secondary }
    }
}

struct AgentWatchSidebarRow: View {
    let session: AgentWatchSession
    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: session.kind.systemImage).frame(width: 24).foregroundStyle(session.requiresAttention ? Color.orange : Color.accentColor)
            VStack(alignment: .leading, spacing: 3) {
                Text(session.displayName).font(.headline)
                Text(session.stateReason).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                Text(relativeText).font(.caption2).foregroundStyle(.tertiary)
            }
            Spacer(); AgentWatchStatusBadge(state: session.state)
        }.padding(.vertical, 5)
    }
    private var relativeText: String { session.lastActivityTime.map { $0.formatted(.relative(presentation: .numeric)) } ?? "activity unavailable" }
}

struct AgentWatchSessionDetail: View {
    let session: AgentWatchSession
    private let activator = AgentSessionActivator()
    var body: some View {
        Form {
            Section("Session") {
                LabeledContent("Agent", value: session.displayName)
                LabeledContent("State", value: session.state.label)
                LabeledContent("Reason", value: session.stateReason)
                LabeledContent("Host", value: session.host)
                LabeledContent("Process ID", value: String(session.processID))
                if let app = session.associatedApplication { LabeledContent("Application", value: app) }
                if let started = session.startTime { LabeledContent("Started", value: started.formatted()) }
            }
            Section("Actions") {
                Button("Return to Session") { _ = activator.activate(session) }
                    .disabled(session.applicationBundleIdentifier == nil)
                Text(session.applicationBundleIdentifier == nil ? "No reliable application target is available." : "Focuses the owning application; exact terminal targeting is not claimed.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }.formStyle(.grouped)
    }
}

/// Monitoring lifetime belongs to the app/service layer, not this view's
/// visibility: `AgentWatchService.start()` is called exactly once, from
/// `RootView`'s `.task` at app launch, and `.stop()` is called exactly once,
/// from `SigilApp`'s `NSApplication.willTerminateNotification` handler.
/// This view (and `AgentWatchMenuBarView` below) must never call `start()`
/// or `stop()` — doing so from `onAppear`/`onDisappear` previously tied
/// background polling and the keep-awake power assertion to whether the
/// Agent Watch sidebar tab happened to be on screen, silently halting
/// monitoring the moment the user navigated to any other section.
struct AgentWatchView: View {
    @ObservedObject var service: AgentWatchService
    @State private var selection: AgentWatchSession.ID?
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                VStack(alignment: .leading) {
                    Text("Agent Watch").font(.largeTitle.bold())
                    Text("\(service.activeCount) Active · \(service.attentionCount) Needs You").foregroundStyle(.secondary)
                }
                Spacer()
                Label(service.isKeepingAwake ? "Keeping Mac Available" : "Keep Awake Inactive", systemImage: service.isKeepingAwake ? "bolt.fill" : "bolt.slash")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if service.sessions.isEmpty {
                ContentUnavailableView("No Agents Detected", systemImage: "binoculars", description: Text("Agent Watch will update automatically when a supported local agent starts."))
            } else {
                HSplitView {
                    List(service.sessions, selection: $selection) { AgentWatchSidebarRow(session: $0).tag($0.id) }.frame(minWidth: 320)
                    if let selected = service.sessions.first(where: { $0.id == selection }) { AgentWatchSessionDetail(session: selected) }
                    else { ContentUnavailableView("Select a Session", systemImage: "sidebar.right") }
                }
            }
        }.padding(24)
    }
}

struct AgentWatchMenuBarView: View {
    @ObservedObject var service: AgentWatchService
    @Environment(\.openWindow) private var openWindow
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Agent Watch").font(.headline)
            Text("\(service.activeCount) active · \(service.attentionCount) needs you").font(.caption).foregroundStyle(.secondary)
            Divider()
            ForEach(service.sessions.prefix(6)) { session in HStack { Text("\(session.displayName) — \(session.state.label)"); Spacer() } }
            if service.sessions.isEmpty { Text("No agents detected").foregroundStyle(.secondary) }
            Divider()
            Button("Open Agent Watch") { openWindow(id: "agent-watch") }
            Text(service.isKeepingAwake ? "Keep Awake: On" : "Keep Awake: Off").font(.caption)
        }.padding().frame(width: 290)
    }
}
