import SwiftUI

/// The Mission Control shell: native sidebar + detail, using standard macOS
/// desktop navigation (`NavigationSplitView`, not an iOS-style stack).
struct RootView: View {
    @State private var selection: SidebarSection? = .overview
    @StateObject private var missionControlStore = MissionControlStore()
    @ObservedObject var agentWatch: AgentWatchService

    init(agentWatch: AgentWatchService) { self.agentWatch = agentWatch }

    var body: some View {
        NavigationSplitView {
            SidebarView(selection: $selection)
        } detail: {
            NavigationStack {
                detailView(for: selection ?? .overview)
                    .navigationTitle((selection ?? .overview).title)
            }
        }
        .task {
            agentWatch.start()
            // Cancelled automatically when RootView leaves the hierarchy;
            // polls at the store's configured interval until then, plus the
            // toolbar button above triggers an immediate manual refresh.
            await missionControlStore.pollContinuously()
        }
    }

    @ViewBuilder
    private func detailView(for section: SidebarSection) -> some View {
        switch section {
        case .overview:
            OverviewView(store: missionControlStore)
        case .portfolio:
            PortfolioView()
        case .proposals:
            ProposalsView()
        case .launch:
            LaunchView()
        case .executions:
            ExecutionsView()
        case .reconciliation:
            ReconciliationView()
        case .audit:
            AuditView()
        case .news:
            NewsView()
        case .agentWatch:
            AgentWatchView(service: agentWatch)
        case .settings:
            SettingsView()
        }
    }
}

#Preview {
    RootView(agentWatch: AgentWatchService())
}
