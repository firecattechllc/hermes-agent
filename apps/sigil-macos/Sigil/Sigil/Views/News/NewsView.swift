import SwiftUI

/// Real governed news evidence store status. `status == "empty"` (nothing
/// ingested yet in this isolated dev environment) is shown honestly as
/// "No Data Yet" rather than invented content — this is a genuine backend
/// source (`governed_news_status`), not a placeholder.
struct NewsView: View {
    @StateObject private var store = NewsStore()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                SectionCard(title: "Governed News", systemImage: "newspaper") {
                    if let status = store.status {
                        VStack(alignment: .leading, spacing: 10) {
                            HStack {
                                Text("Status")
                                Spacer()
                                StatusBadge(state: status.status == "ready" ? .connected : .noData)
                            }
                            LabeledContent("Headlines", value: "\(status.headlineCount)")
                            LabeledContent("Symbols covered", value: "\(status.symbolCount)")
                            LabeledContent("Last collected", value: status.lastCollectedAt ?? "Waiting for first configured collection")
                        }
                    } else if let error = store.errorMessage {
                        StatusRowView(entry: StatusEntry(title: "News", state: .offline, detail: error))
                    } else {
                        ProgressView().padding()
                    }
                }

                if let status = store.status {
                    SectionCard(title: "Recent Headlines", systemImage: "text.justify") {
                        RecordListView(records: status.headlines, emptyMessage: "No headlines ingested yet.")
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
