import AppKit
import SwiftUI

@main
struct SigilApp: App {
    @StateObject private var agentWatch = AgentWatchService()

    var body: some Scene {
        WindowGroup {
            RootView(agentWatch: agentWatch)
                .onReceive(NotificationCenter.default.publisher(for: NSApplication.willTerminateNotification)) { _ in
                    agentWatch.stop()
                }
        }
        WindowGroup("Agent Watch", id: "agent-watch") { AgentWatchView(service: agentWatch) }
        MenuBarExtra("Agent Watch", systemImage: agentWatch.attentionCount > 0 ? "exclamationmark.bubble.fill" : "binoculars") {
            AgentWatchMenuBarView(service: agentWatch)
        }
        .windowResizability(.contentSize)
        .commands {
            SidebarCommands()
        }
    }
}
