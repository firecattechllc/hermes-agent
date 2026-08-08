import Foundation
import IOKit.pwr_mgt

@MainActor protocol AgentPowerManaging: AnyObject {
    var isKeepingAwake: Bool { get }
    func update(shouldKeepAwake: Bool)
    func releaseAssertion()
}

@MainActor final class AgentPowerManager: AgentPowerManaging {
    private var assertionID = IOPMAssertionID(0)
    private(set) var isKeepingAwake = false

    func update(shouldKeepAwake: Bool) {
        guard shouldKeepAwake != isKeepingAwake else { return }
        if shouldKeepAwake {
            let result = IOPMAssertionCreateWithName(
                kIOPMAssertionTypePreventUserIdleSystemSleep as CFString,
                IOPMAssertionLevel(kIOPMAssertionLevelOn),
                "Sigil Agent Watch: an agent is actively working" as CFString,
                &assertionID
            )
            isKeepingAwake = result == kIOReturnSuccess
        } else { releaseAssertion() }
    }

    func releaseAssertion() {
        if assertionID != 0 { IOPMAssertionRelease(assertionID) }
        assertionID = 0
        isKeepingAwake = false
    }

    deinit {
        if assertionID != 0 { IOPMAssertionRelease(assertionID) }
    }
}

struct AgentBatteryProtection: Equatable, Sendable {
    var minimumBatteryPercent: Int? = nil
}
