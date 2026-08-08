import SwiftUI

/// Small pill showing a `ServiceState` (e.g. "Demo Data", "Disabled").
struct StatusBadge: View {
    let state: ServiceState

    var body: some View {
        Label(state.label, systemImage: state.systemImage)
            .font(.caption.weight(.medium))
            .foregroundStyle(state.tint)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(state.tint.opacity(0.12), in: Capsule())
    }
}

#Preview {
    VStack(alignment: .leading, spacing: 8) {
        StatusBadge(state: .connected)
        StatusBadge(state: .mockData)
        StatusBadge(state: .degraded)
        StatusBadge(state: .disabled)
        StatusBadge(state: .offline)
    }
    .padding()
}
