import SwiftUI

/// Top-level sidebar destinations for the Sigil 4.0 Mission Control shell.
enum SidebarSection: String, CaseIterable, Identifiable, Hashable {
    case overview
    case portfolio
    case proposals
    case launch
    case executions
    case reconciliation
    case audit
    case news
    case agentWatch
    case settings

    var id: String { rawValue }

    var title: String {
        switch self {
        case .overview: return "Overview"
        case .portfolio: return "Portfolio"
        case .proposals: return "Proposals"
        case .launch: return "Launch"
        case .executions: return "Executions"
        case .reconciliation: return "Reconciliation"
        case .audit: return "Audit"
        case .news: return "News"
        case .agentWatch: return "Agent Watch"
        case .settings: return "Settings"
        }
    }

    var systemImage: String {
        switch self {
        case .overview: return "square.grid.2x2"
        case .portfolio: return "chart.pie"
        case .proposals: return "doc.text.magnifyingglass"
        case .launch: return "paperplane"
        case .executions: return "checkmark.circle"
        case .reconciliation: return "arrow.triangle.2.circlepath"
        case .audit: return "list.clipboard"
        case .news: return "newspaper"
        case .agentWatch: return "binoculars"
        case .settings: return "gearshape"
        }
    }
}
