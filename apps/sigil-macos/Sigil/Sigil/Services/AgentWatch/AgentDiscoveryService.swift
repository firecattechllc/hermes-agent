import AppKit
import Darwin
import Foundation

nonisolated protocol AgentProcessProviding: Sendable { func snapshots() -> [AgentProcessSnapshot] }

nonisolated struct NativeAgentProcessProvider: AgentProcessProviding {
    nonisolated func snapshots() -> [AgentProcessSnapshot] {
        var capacity = 2048
        var pids = [pid_t](repeating: 0, count: capacity)
        let bytes = proc_listallpids(&pids, Int32(capacity * MemoryLayout<pid_t>.size))
        guard bytes > 0 else { return [] }
        capacity = Int(bytes) / MemoryLayout<pid_t>.size
        let applications = Dictionary(uniqueKeysWithValues: NSWorkspace.shared.runningApplications.compactMap { app in
            app.processIdentifier > 0 ? (app.processIdentifier, app) : nil
        })
        return pids.prefix(capacity).filter { $0 > 0 }.compactMap { pid in
            var pathBuffer = [CChar](repeating: 0, count: 4096)
            guard proc_pidpath(pid, &pathBuffer, UInt32(pathBuffer.count)) > 0 else { return nil }
            let path = String(cString: pathBuffer)
            let app = applications[pid]
            return AgentProcessSnapshot(
                processID: pid,
                parentProcessID: parentPID(pid),
                executablePath: path,
                executableName: URL(fileURLWithPath: path).lastPathComponent,
                arguments: [],
                startTime: startTime(pid),
                associatedApplication: app?.localizedName,
                applicationBundleIdentifier: app?.bundleIdentifier
            )
        }
    }

    nonisolated private func parentPID(_ pid: pid_t) -> pid_t? {
        var info = proc_bsdinfo()
        let size = proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &info, Int32(MemoryLayout.size(ofValue: info)))
        return size > 0 ? pid_t(info.pbi_ppid) : nil
    }

    nonisolated private func startTime(_ pid: pid_t) -> Date? {
        var info = proc_bsdinfo()
        let size = proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &info, Int32(MemoryLayout.size(ofValue: info)))
        guard size > 0 else { return nil }
        return Date(timeIntervalSince1970: TimeInterval(info.pbi_start_tvsec))
    }
}

nonisolated struct AgentDiscoveryService: Sendable {
    var provider: any AgentProcessProviding = NativeAgentProcessProvider()
    var classifier = AgentDiscoveryClassifier()

    nonisolated init(provider: any AgentProcessProviding = NativeAgentProcessProvider(), classifier: AgentDiscoveryClassifier = AgentDiscoveryClassifier()) {
        self.provider = provider
        self.classifier = classifier
    }

    nonisolated func discover(now: Date = .now) -> [AgentWatchSession] {
        provider.snapshots().compactMap { process in
            guard let kind = classifier.kind(for: process) else { return nil }
            return AgentWatchSession(
                id: "\(kind.rawValue):\(process.processID):\(Int(process.startTime?.timeIntervalSince1970 ?? 0))",
                kind: kind, displayName: kind.displayName, state: .unknown,
                stateReason: "Process detected; no reliable activity signal",
                processID: process.processID, parentProcessID: process.parentProcessID,
                host: "This Mac", workingDirectory: nil,
                associatedApplication: process.associatedApplication,
                applicationBundleIdentifier: process.applicationBundleIdentifier,
                startTime: process.startTime, lastActivityTime: nil, lastStateChangeTime: now
            )
        }
    }
}
