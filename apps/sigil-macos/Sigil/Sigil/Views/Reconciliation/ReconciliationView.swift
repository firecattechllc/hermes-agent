import SwiftUI

/// Real reconciliation state from the governed paper runtime — when
/// reconciliation last completed, and any degraded conditions or unmanaged
/// positions it surfaced. This screen only reads existing state; it does
/// not trigger a new reconciliation pass (that is a distinct governed
/// mutation, out of scope for a status display).
struct ReconciliationView: View {
    @StateObject private var store = PaperExecutionStore()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                SectionCard(title: "Reconciliation", systemImage: "arrow.triangle.2.circlepath") {
                    if let status = store.status {
                        VStack(alignment: .leading, spacing: 10) {
                            LabeledContent("Last reconciliation", value: status.lastReconciliation ?? "Never")
                            HStack(alignment: .top) {
                                Text("Degraded conditions")
                                Spacer()
                                StatusBadge(state: status.degradedConditions.isEmpty ? .connected : .degraded)
                            }
                            if !status.degradedConditions.isEmpty {
                                Text(status.degradedConditions.joined(separator: ", "))
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            HStack(alignment: .top) {
                                Text("Unmanaged positions")
                                Spacer()
                                StatusBadge(state: status.unmanagedPositionSymbols.isEmpty ? .connected : .degraded)
                            }
                            if !status.unmanagedPositionSymbols.isEmpty {
                                Text(status.unmanagedPositionSymbols.joined(separator: ", "))
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    } else if let error = store.errorMessage {
                        StatusRowView(entry: StatusEntry(title: "Reconciliation", state: .offline, detail: error))
                    } else {
                        ProgressView().padding()
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
    }
}
