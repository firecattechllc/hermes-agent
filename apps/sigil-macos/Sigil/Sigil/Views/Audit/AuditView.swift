import SwiftUI

/// Real audit trail entries from the governed paper runtime — every
/// lifecycle action (activate/deactivate/pause/resume/reconcile) writes an
/// entry here with its own evidence identity. Previously unreachable from
/// this client; `recent_audit` was added to the backend's own allow-list
/// as a pure read-only exposure of an already-implemented collection.
struct AuditView: View {
    @StateObject private var store = PaperCollectionStore(fetch: { try await HermesBridgeClient().recentAudit() })

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                SectionCard(title: "Audit Trail", systemImage: "list.clipboard") {
                    if let error = store.errorMessage {
                        StatusRowView(entry: StatusEntry(title: "Audit", state: .offline, detail: error))
                    } else if let result = store.result {
                        RecordListView(records: result.items, emptyMessage: "No audit entries recorded yet.")
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
