import SwiftUI

/// Real paper execution history — submitted orders and fills from the
/// governed local paper runtime. No live brokerage authority anywhere:
/// `broker_submission` reflects Sigil's own paper simulation flag, never a
/// connection to a real brokerage.
struct ExecutionsView: View {
    @StateObject private var ordersStore = PaperCollectionStore(fetch: { try await HermesBridgeClient().paperOrders() })
    @StateObject private var fillsStore = PaperCollectionStore(fetch: { try await HermesBridgeClient().paperFills() })

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                SectionCard(title: "Paper Orders", systemImage: "checkmark.circle") {
                    if let error = ordersStore.errorMessage {
                        StatusRowView(entry: StatusEntry(title: "Orders", state: .offline, detail: error))
                    } else if let result = ordersStore.result {
                        RecordListView(records: result.items, emptyMessage: "No paper orders submitted yet.")
                    } else {
                        ProgressView().padding()
                    }
                }

                SectionCard(title: "Paper Fills", systemImage: "checkmark.seal") {
                    if let error = fillsStore.errorMessage {
                        StatusRowView(entry: StatusEntry(title: "Fills", state: .offline, detail: error))
                    } else if let result = fillsStore.result {
                        RecordListView(records: result.items, emptyMessage: "No paper fills recorded yet.")
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
                    Task {
                        await ordersStore.refresh()
                        await fillsStore.refresh()
                    }
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
            }
        }
        .task {
            await ordersStore.refresh()
            await fillsStore.refresh()
        }
    }
}
