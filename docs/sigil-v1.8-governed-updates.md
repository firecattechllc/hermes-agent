# Sigil Desktop v1.8 Governed Updates

Sigil v1.8 uses `electron-updater` behind a main-process state machine. The
renderer receives snapshots through a fixed preload API; it cannot choose a
URL, file, channel, or command. Production checks run shortly after packaged
startup. Development checks are disabled unless `SIGIL_ENABLE_DEV_UPDATES=1`.

States are `idle`, `checking`, `update-available`, `up-to-date`,
`downloading`, `downloaded`, `installing`, `deferred`, `failed`, and
`disabled`. Downloads require operator approval. Installation requires an
explicit Restart and Install action and two immediate readiness checks. A
running, locked, corrupt, or recovery-required paper runtime blocks install.
Paper-runtime state remains in the existing user-data directory and is not
part of the application bundle replaced by an update.

## Internal test channel

Set both `SIGIL_ENABLE_DEV_UPDATES=1` and
`SIGIL_INTERNAL_UPDATE_CHANNEL=1` only in an isolated test profile. The UI
shows `INTERNAL TEST UPDATE`, prereleases are enabled, and the channel is
`internal`. Production never enables this automatically. Build and publish
`1.8.0-test.1`, then `1.8.0-test.2`, with ZIP, DMG, blockmap, and
`latest-mac.yml` assets. Verify check, approval, progress, defer, restart
approval, protected-operation blocking, and restored paper state. Tests use
fakes and never contact GitHub or Apple.

## Release workflow

Run `npm run update:check-config`, `npm run update:test`, and
`npm run release:unsigned-test` from `apps/sigil-desktop`. Ordinary builds
always use `--publish never`; upload assets to a GitHub prerelease only as a
separate operator-approved step. `release:verify` writes a machine-readable
report under `release/certification`.

`release:mac:notarized` fails closed unless both a Developer ID Application
identity and either Apple ID credentials (`APPLE_ID`,
`APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID`) or App Store Connect
credentials (`APPLE_API_KEY`, `APPLE_API_KEY_ID`, `APPLE_API_ISSUER`) exist.
Secret values are never printed. Verification uses strict `codesign`, `spctl`,
and `xcrun stapler validate`.

A regular Apple ID cannot create the Developer ID Application certificate
needed for warning-free public distribution. Unsigned internal updater testing
is supported now, but it is never described as notarized or Gatekeeper-trusted.
If an update is interrupted or metadata/network/checksum validation fails, the
state moves to `failed`; the installed application and paper data remain
unchanged, and the operator can retry a check. Downgrades and equal versions
are rejected.
