import SwiftUI

/// Real, read-only paper portfolio state from the governed backend —
/// deployed capital, governed allocation headroom, and open positions.
/// Never a broker connection; this is Sigil's own local paper ledger.
struct PortfolioView: View {
    @StateObject private var statusStore = PaperExecutionStore()
    @StateObject private var positionsStore = PaperCollectionStore(fetch: { try await HermesBridgeClient().paperPositions() })

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                if let status = statusStore.status {
                    SectionCard(title: "Paper Portfolio", systemImage: "chart.pie") {
                        VStack(alignment: .leading, spacing: 10) {
                            LabeledContent("Deployed paper capital", value: status.deployedPaperCapital)
                            LabeledContent("Remaining governed allocation", value: status.remainingGovernedAllocation)
                            LabeledContent("Open positions", value: "\(status.openPositions)")
                            LabeledContent("Broker", value: status.broker)
                            HStack {
                                Text("Broker submission")
                                Spacer()
                                StatusBadge(state: status.brokerSubmission ? .degraded : .disabled)
                            }
                        }
                    }
                } else if let error = statusStore.errorMessage {
                    SectionCard(title: "Paper Portfolio", systemImage: "chart.pie") {
                        StatusRowView(entry: StatusEntry(title: "Status", state: .offline, detail: error))
                    }
                } else {
                    ProgressView().padding()
                }

                SectionCard(title: "Positions", systemImage: "list.bullet.rectangle") {
                    if let error = positionsStore.errorMessage {
                        StatusRowView(entry: StatusEntry(title: "Positions", state: .offline, detail: error))
                    } else if let result = positionsStore.result {
                        RecordListView(records: result.items, emptyMessage: "No open paper positions.")
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
                        await statusStore.refresh()
                        await positionsStore.refresh()
                    }
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
            }
        }
        .task {
            await statusStore.refresh()
            await positionsStore.refresh()
        }
    }
}
