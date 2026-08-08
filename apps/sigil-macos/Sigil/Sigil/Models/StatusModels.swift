import SwiftUI

/// Connection/data-provenance state for any status shown in Mission Control.
///
/// `.mockData` means a value was never queried from the backend at all
/// (Phase 1 style placeholder). `.unavailable` means the real backend WAS
/// queried and honestly reported that a field is empty/unconfigured/not
/// registered — that is real information, just not a green state. `.connected`
/// must only be used when the backend's own semantic health field says
/// healthy — never merely because an HTTP request succeeded.
enum ServiceState: String, Hashable {
    case connected
    case mockData
    case degraded
    case disabled
    case unavailable
    case offline

    var label: String {
        switch self {
        case .connected: return "Connected"
        case .mockData: return "Demo Data"
        case .degraded: return "Degraded"
        case .disabled: return "Disabled"
        case .unavailable: return "Unavailable"
        case .offline: return "Offline"
        }
    }

    var tint: Color {
        switch self {
        case .connected: return .green
        case .mockData: return .orange
        case .degraded: return .yellow
        case .disabled: return .secondary
        case .unavailable: return .blue
        case .offline: return .red
        }
    }

    var systemImage: String {
        switch self {
        case .connected: return "checkmark.circle.fill"
        case .mockData: return "flask.fill"
        case .degraded: return "exclamationmark.triangle.fill"
        case .disabled: return "slash.circle"
        case .unavailable: return "questionmark.circle"
        case .offline: return "xmark.circle.fill"
        }
    }
}

/// A single labeled status entry (e.g. "Hermes Orchestration: Demo Data").
struct StatusEntry: Identifiable, Hashable {
    let id = UUID()
    let title: String
    let state: ServiceState
    let detail: String
}
