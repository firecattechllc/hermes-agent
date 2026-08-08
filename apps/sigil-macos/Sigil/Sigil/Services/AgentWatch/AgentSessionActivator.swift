import AppKit

enum AgentSessionActivationResult: Equatable { case applicationFocused, unavailable }

struct AgentSessionActivator {
    func activate(_ session: AgentWatchSession) -> AgentSessionActivationResult {
        guard let bundleID = session.applicationBundleIdentifier,
              let application = NSRunningApplication.runningApplications(withBundleIdentifier: bundleID).first
        else { return .unavailable }
        return application.activate(options: [.activateAllWindows]) ? .applicationFocused : .unavailable
    }
}
