import Testing
@testable import SigilDev

/// `ServiceState` is the app's one shared vocabulary for "how healthy is
/// this value" — these tests pin down its completeness and the rule that
/// must never be violated: `.connected` (green) is a distinct, deliberate
/// state, not a default.
struct StatusSemanticsTests {
    @Test func everyServiceStateHasANonEmptyLabel() {
        for state: ServiceState in [.connected, .mockData, .degraded, .disabled, .optional, .notConfigured, .noData, .unavailable, .offline] {
            #expect(!state.label.isEmpty)
        }
    }

    @Test func everyServiceStateHasASystemImage() {
        for state: ServiceState in [.connected, .mockData, .degraded, .disabled, .optional, .notConfigured, .noData, .unavailable, .offline] {
            #expect(!state.systemImage.isEmpty)
        }
    }

    @Test func expectedAbsenceStatesAreNotErrors() {
        for state: ServiceState in [.optional, .notConfigured, .noData] {
            #expect(state.tint != ServiceState.offline.tint)
            #expect(state.tint != ServiceState.degraded.tint)
        }
    }

    @Test func backendStateClassificationDistinguishesConfigurationAndFirstRun() {
        #expect(HermesBridgeDataProvider.state(forBackendHealth: "not_configured") == .notConfigured)
        #expect(HermesBridgeDataProvider.state(forBackendHealth: "unconfigured") == .notConfigured)
        #expect(HermesBridgeDataProvider.state(forBackendHealth: "empty") == .noData)
        #expect(HermesBridgeDataProvider.state(forConnection: "not_configured") == .notConfigured)
        #expect(HermesBridgeDataProvider.state(forConnection: "disconnected") == .offline)
    }

    @Test func emptyEvidenceHealthIsNoDataNotConnected() {
        let state = HermesBridgeDataProvider.combinedEvidenceState(ledgerState: .noData, artifactState: .noData)
        #expect(state == .noData)
        #expect(state != .connected)
    }

    @Test func mockDataAndUnavailableAreVisuallyDistinctFromConnected() {
        // Demo data (orange) and honestly-unavailable (blue) must never
        // share a tint with a genuinely healthy connected state (green).
        #expect(ServiceState.mockData.tint != ServiceState.connected.tint)
        #expect(ServiceState.unavailable.tint != ServiceState.connected.tint)
        #expect(ServiceState.mockData.tint != ServiceState.unavailable.tint)
    }

    @Test func offlineAndDisabledAreDistinctFromConnected() {
        #expect(ServiceState.offline.tint != ServiceState.connected.tint)
        #expect(ServiceState.disabled.tint != ServiceState.connected.tint)
    }

    @Test func statusEntryPreservesAllFields() {
        let entry = StatusEntry(title: "Example", state: .degraded, detail: "some detail")
        #expect(entry.title == "Example")
        #expect(entry.state == .degraded)
        #expect(entry.detail == "some detail")
    }
}
