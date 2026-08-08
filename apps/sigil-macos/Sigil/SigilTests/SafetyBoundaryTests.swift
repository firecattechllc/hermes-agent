import Testing
@testable import SigilDev

/// These are the highest-priority tests in the whole suite: they assert the
/// client-enforced safety guarantees that must hold regardless of backend
/// state, network reachability, or any bug elsewhere in the app.
struct SafetyBoundaryTests {
    @Test func safetyPostureIsAlwaysPaperOnly() {
        #expect(SafetyPosture.current.isPaperOnly == true)
    }

    @Test func safetyPostureNeverHasLiveExecutionAuthority() {
        #expect(SafetyPosture.current.hasLiveExecutionAuthority == false)
    }

    @Test func safetyPostureNeverHasBrokerSubmissionEnabledByDefault() {
        #expect(SafetyPosture.current.isBrokerSubmissionEnabled == false)
    }

    @Test func safetyPostureCarriesNonEmptyGovernanceNotes() {
        #expect(!SafetyPosture.current.brokerGovernanceNote.isEmpty)
        #expect(!SafetyPosture.current.authorityNote.isEmpty)
    }

    /// Every `MissionControlSnapshot`, no matter which provider built it,
    /// must carry this same safety posture — the client's refusal to expose
    /// execution authority must never be something a provider can override.
    @Test func mockSnapshotSafetyMatchesCurrentPosture() async {
        let snapshot = await MockMissionControlDataProvider().fetchSnapshot()
        #expect(snapshot.safety.isPaperOnly == true)
        #expect(snapshot.safety.hasLiveExecutionAuthority == false)
        #expect(snapshot.safety.isBrokerSubmissionEnabled == false)
    }
}
