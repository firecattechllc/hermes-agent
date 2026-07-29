# Sigil Production Auto-Update Checklist

| Status | Current release | Target |
| --- | --- | --- |
| In progress | v1.8.0 | Production auto-updates beginning with v1.9 |

## Completed in v1.8 ✅

The governed auto-update infrastructure has been implemented.

### Updater Framework

- [x] Governed updater state machine
- [x] Version comparison
- [x] Equal-version rejection
- [x] Downgrade protection
- [x] Internal prerelease update channel
- [x] Explicit user approval before download
- [x] Explicit approval before restart/install
- [x] Audit logging

### Desktop Integration

- [x] Electron main-process integration
- [x] Secure preload bridge
- [x] Mission Control updater UI
- [x] IPC wiring
- [x] Runtime install safety checks

### Release Pipeline

- [x] DMG generation
- [x] ZIP generation
- [x] `latest-mac.yml` generation
- [x] Blockmap generation
- [x] Release certification
- [x] Packaged runtime validation
- [x] Packaged Python compile verification
- [x] `runtime_snapshot` verification

## Remaining Before Public Production

### Apple Developer

- [ ] Enroll in the Apple Developer Program
- [ ] Create a Developer ID Application certificate
- [ ] Configure the signing identity
- [ ] Configure notarization credentials
- [ ] Configure stapling
- [ ] Sign production release artifacts
- [ ] Complete Apple notarization
- [ ] Staple the notarization ticket

### Release Hosting

- [ ] Host `latest-mac.yml`
- [ ] Host the signed ZIP package
- [ ] Host blockmaps
- [ ] Configure the production update feed
- [ ] Verify update feed availability

### Production Validation

- [ ] Publish signed v1.9
- [ ] Verify update detection from v1.8
- [ ] Verify the governed approval workflow
- [ ] Verify signature validation
- [ ] Verify restart and installation
- [ ] Verify the application launches as v1.9
- [ ] Verify rollback protection

## Expected Production Flow

```text
v1.8 running
    ↓
Check update feed
    ↓
v1.9 detected
    ↓
Governed approval required
    ↓
Download signed package
    ↓
Verify signature
    ↓
Restart and install
    ↓
Launch v1.9
```

## Goal

Once the Apple Developer certificate and notarization pipeline are complete, Sigil will support governed production auto-updates. Future releases—including v1.9, v2.0, v2.1, and beyond—can be delivered directly through the built-in updater without requiring users to manually download and install a new DMG.

This milestone completes Sigil's transition from manual desktop releases to a governed, self-updating desktop application.
