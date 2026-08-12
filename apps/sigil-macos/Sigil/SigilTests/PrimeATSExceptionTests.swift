import Foundation
import Testing

@Suite("Prime ATS Exception")
struct PrimeATSExceptionTests {
    @Test("HermesBridgeService permits insecure HTTP only to Tailscale CGNAT")
    func tailscaleATSExceptionIsNarrow() throws {
        let testFile = URL(fileURLWithPath: #filePath)
        let projectRoot = testFile
            .deletingLastPathComponent() // SigilTests
            .deletingLastPathComponent() // Sigil
        let infoURL = projectRoot
            .appendingPathComponent("HermesBridgeService")
            .appendingPathComponent("Info.plist")

        let data = try Data(contentsOf: infoURL)

        guard
            let plist = try PropertyListSerialization.propertyList(
                from: data,
                options: [],
                format: nil
            ) as? [String: Any]
        else {
            Issue.record("HermesBridgeService Info.plist is not a dictionary")
            return
        }

        let ats = plist["NSAppTransportSecurity"] as? [String: Any]
        #expect(ats != nil)

        // Broad ATS disablement must never be used.
        #expect(ats?["NSAllowsArbitraryLoads"] as? Bool != true)

        let exceptionDomains = ats?["NSExceptionDomains"] as? [String: Any]
        #expect(exceptionDomains != nil)

        let tailscale = exceptionDomains?["100.64.0.0/10"] as? [String: Any]
        #expect(tailscale != nil)
        #expect(tailscale?["NSExceptionAllowsInsecureHTTPLoads"] as? Bool == true)

        // This regression guard should stay scoped to the Tailscale CGNAT range.
        #expect(exceptionDomains?.count == 1)
    }
}
