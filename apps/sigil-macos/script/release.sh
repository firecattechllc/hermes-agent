#!/usr/bin/env bash
#
# Reproducible release pipeline for the native Sigil macOS app: archive,
# Developer ID sign, notarize, staple, and verify — from a clean checkout of
# `apps/sigil-macos`, in the Production build configuration (the same
# configuration confirmed to match the currently-shipped /Applications/Sigil.app:
# bundle id com.firecattechnology.sigil.macos, Developer ID Application signing,
# team VJ4ADMST5U).
#
# CREDENTIAL MODEL — read this before running:
#   This script never accepts, stores, prints, or embeds a secret of any kind.
#   - Code signing uses the "Developer ID Application: Matthew Callaham
#     (VJ4ADMST5U)" identity already present in this Mac's login keychain
#     (Keychain Access / `security find-identity -v -p codesigning`). If it's
#     not there, install/renew it via Xcode > Settings > Accounts first; this
#     script does not and cannot provision it.
#   - Notarization uses `notarytool`'s keychain-profile mechanism: run, once,
#     out of band, NEVER inside this script or any CI log:
#       xcrun notarytool store-credentials sigil-notarize \
#         --apple-id <your-apple-id> --team-id VJ4ADMST5U --password <app-specific-password>
#     That stores the credential in this Mac's keychain under the profile
#     name "sigil-notarize" (override with $SIGIL_NOTARY_PROFILE). This
#     script only ever references that NAME — the credential itself never
#     appears in source, logs, environment dumps, or this script's output.
#   - For CI: `notarytool store-credentials` also supports an App Store
#     Connect API key (`--key`/`--key-id`/`--issuer`), which can be wired to
#     a CI secrets store and materialized into a runner's keychain at job
#     start — this script is agnostic to which credential type backs the
#     profile name.
#
# FAIL-CLOSED: every step is checked explicitly; the script `set -e`s and
# also verifies each critical step's actual result (not just exit code)
# before proceeding to the next. On any failure it stops immediately.
#
# Usage:
#   script/release.sh                 # full pipeline: build, sign, notarize, staple, verify
#   script/release.sh --skip-notarize # build, sign, verify signature only — stop before
#                                      # any network call to Apple or artifact submission
#   script/release.sh --allow-dirty   # permit a non-clean git working tree (default: refuse)
#
# Output: apps/sigil-macos/.release/<version>-b<build>-<shortsha>/
#   Sigil.xcarchive, Sigil.app, Sigil-<version>.zip (if notarizing), manifest.json

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"          # apps/sigil-macos
PROJECT="$APP_DIR/Sigil/Sigil.xcodeproj"
SCHEME="Sigil"
CONFIGURATION="Production"
TEAM_ID="VJ4ADMST5U"
EXPECTED_BUNDLE_ID="com.firecattechnology.sigil.macos"
SIGNING_IDENTITY_NAME="Developer ID Application: Matthew Callaham (VJ4ADMST5U)"
NOTARY_PROFILE="${SIGIL_NOTARY_PROFILE:-sigil-notarize}"
EXPORT_OPTIONS="$SCRIPT_DIR/ExportOptions.plist"

SKIP_NOTARIZE=0
ALLOW_DIRTY=0
for arg in "$@"; do
  case "$arg" in
    --skip-notarize) SKIP_NOTARIZE=1 ;;
    --allow-dirty) ALLOW_DIRTY=1 ;;
    --help|-h)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "error: unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

log() { echo "[release] $*"; }
fail() { echo "[release] FAILED: $*" >&2; exit 1; }

command -v xcodebuild >/dev/null 2>&1 || fail "xcodebuild not found — Xcode command line tools required"
command -v security >/dev/null 2>&1 || fail "security(1) not found"
[ -d "$PROJECT" ] || fail "project not found at $PROJECT"
[ -f "$EXPORT_OPTIONS" ] || fail "ExportOptions.plist not found at $EXPORT_OPTIONS"

# --- 1. Git state: tie every artifact to a known, ideally clean commit ---
cd "$APP_DIR"
GIT_ROOT="$(git rev-parse --show-toplevel)"
BRANCH="$(git -C "$GIT_ROOT" rev-parse --abbrev-ref HEAD)"
SHA="$(git -C "$GIT_ROOT" rev-parse HEAD)"
SHORT_SHA="$(git -C "$GIT_ROOT" rev-parse --short HEAD)"
if [ "$ALLOW_DIRTY" -eq 0 ]; then
  if [ -n "$(git -C "$GIT_ROOT" status --short -- apps/sigil-macos)" ]; then
    fail "apps/sigil-macos has uncommitted changes — commit or pass --allow-dirty. This keeps the resulting artifact traceable to an exact commit, which is the whole point of this pipeline (see the certification finding this script exists to close)."
  fi
fi
log "building from $BRANCH @ $SHA"

# --- 2. Signing identity must already exist locally; never provisioned here ---
if ! security find-identity -v -p codesigning 2>/dev/null | grep -qF "$SIGNING_IDENTITY_NAME"; then
  fail "signing identity '$SIGNING_IDENTITY_NAME' not found in keychain. This script does not create or import identities — install it via Xcode > Settings > Accounts first."
fi
log "signing identity present: $SIGNING_IDENTITY_NAME"

# --- 3. Notarization credential profile must already exist; never created or read here ---
if [ "$SKIP_NOTARIZE" -eq 0 ]; then
  if ! xcrun notarytool history --keychain-profile "$NOTARY_PROFILE" >/dev/null 2>&1; then
    fail "notarytool keychain profile '$NOTARY_PROFILE' not found or invalid. Create it once, out of band: xcrun notarytool store-credentials $NOTARY_PROFILE --apple-id <id> --team-id $TEAM_ID --password <app-specific-password> (or --key/--key-id/--issuer for an API key). This check itself makes a read-only call to Apple to confirm the profile authenticates; it submits nothing."
  fi
  log "notarytool profile '$NOTARY_PROFILE' present and valid"
fi

# --- 4. Resolve version from the project itself (single source of truth) ---
BUILD_SETTINGS="$(xcodebuild -project "$PROJECT" -target Sigil -configuration "$CONFIGURATION" -showBuildSettings 2>/dev/null)" \
  || fail "xcodebuild -showBuildSettings failed while resolving version/bundle id — is the project readable and the scheme name still 'Sigil'?"
MARKETING_VERSION="$(echo "$BUILD_SETTINGS" | awk -F' = ' '/ MARKETING_VERSION / {print $2; exit}')"
BUILD_NUMBER="$(echo "$BUILD_SETTINGS" | awk -F' = ' '/ CURRENT_PROJECT_VERSION / {print $2; exit}')"
ACTUAL_BUNDLE_ID="$(echo "$BUILD_SETTINGS" | awk -F' = ' '/ PRODUCT_BUNDLE_IDENTIFIER / {print $2; exit}')"
[ "$ACTUAL_BUNDLE_ID" = "$EXPECTED_BUNDLE_ID" ] || fail "unexpected bundle id for $CONFIGURATION: got '$ACTUAL_BUNDLE_ID', expected '$EXPECTED_BUNDLE_ID'"
[ -n "$MARKETING_VERSION" ] || fail "could not resolve MARKETING_VERSION from the project"
log "releasing Sigil $MARKETING_VERSION (build $BUILD_NUMBER), bundle id $ACTUAL_BUNDLE_ID"

# --- 5. Deterministic output location, named for exactly what produced it ---
OUT_DIR="$APP_DIR/.release/${MARKETING_VERSION}-b${BUILD_NUMBER}-${SHORT_SHA}"
rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"
ARCHIVE_PATH="$OUT_DIR/Sigil.xcarchive"
EXPORT_DIR="$OUT_DIR/export"
APP_PATH="$EXPORT_DIR/Sigil.app"
ZIP_PATH="$OUT_DIR/Sigil-${MARKETING_VERSION}.zip"
MANIFEST_PATH="$OUT_DIR/manifest.json"

# --- 6. Archive ---
log "archiving (this takes a few minutes)..."
xcodebuild archive \
  -project "$PROJECT" \
  -scheme "$SCHEME" \
  -configuration "$CONFIGURATION" \
  -archivePath "$ARCHIVE_PATH" \
  -destination 'generic/platform=macOS' \
  CODE_SIGN_STYLE=Manual \
  CODE_SIGN_IDENTITY="Developer ID Application" \
  DEVELOPMENT_TEAM="$TEAM_ID" \
  || fail "xcodebuild archive failed"
[ -d "$ARCHIVE_PATH" ] || fail "archive step reported success but $ARCHIVE_PATH does not exist"

# --- 7. Export (Developer ID, not App Store) ---
log "exporting signed .app..."
xcodebuild -exportArchive \
  -archivePath "$ARCHIVE_PATH" \
  -exportPath "$EXPORT_DIR" \
  -exportOptionsPlist "$EXPORT_OPTIONS" \
  || fail "xcodebuild -exportArchive failed"
[ -d "$APP_PATH" ] || fail "export step reported success but $APP_PATH does not exist"

# --- 8. Verify signature (never trust the build step alone) ---
log "verifying code signature..."
codesign --verify --deep --strict --verbose=4 "$APP_PATH" || fail "codesign --verify failed on $APP_PATH"
SIGN_SUMMARY="$(codesign -dvv "$APP_PATH" 2>&1)"
echo "$SIGN_SUMMARY" | grep -qF "$SIGNING_IDENTITY_NAME" || fail "exported app is not signed with the expected identity ($SIGNING_IDENTITY_NAME)"
CODESIGN_TEAM="$(echo "$SIGN_SUMMARY" | awk -F'=' '/^TeamIdentifier=/ {print $2; exit}')"
[ "$CODESIGN_TEAM" = "$TEAM_ID" ] || fail "unexpected TeamIdentifier: got '$CODESIGN_TEAM', expected '$TEAM_ID'"
ACTUAL_APP_BUNDLE_ID="$(defaults read "$APP_PATH/Contents/Info.plist" CFBundleIdentifier)"
[ "$ACTUAL_APP_BUNDLE_ID" = "$EXPECTED_BUNDLE_ID" ] || fail "exported app bundle id mismatch: got '$ACTUAL_APP_BUNDLE_ID'"
log "signature verified: $SIGNING_IDENTITY_NAME, team $CODESIGN_TEAM, bundle id $ACTUAL_APP_BUNDLE_ID"

# --- 9. Confirm the Hermes Bridge XPC service (the artifact this whole
#         reconciliation effort was about) actually made it into the export ---
[ -d "$APP_PATH/Contents/XPCServices/HermesBridgeService.xpc" ] \
  || fail "HermesBridgeService.xpc is missing from the exported app — the release would silently regress the same P0 gap this pipeline exists to close"
log "HermesBridgeService.xpc present in exported bundle"

# --- 10. Pre-notarization Gatekeeper check — EXPECTED to reject at this
#          stage (not stapled yet); this documents that expectation instead
#          of silently treating it as pass/fail noise.
if spctl -a -vv --type execute "$APP_PATH" >/tmp/sigil-release-spctl-pre.$$ 2>&1; then
  log "note: spctl already accepts this build before notarization — unusual but not an error"
else
  log "spctl correctly rejects the unnotarized export (expected at this stage): $(tail -1 /tmp/sigil-release-spctl-pre.$$)"
fi
rm -f /tmp/sigil-release-spctl-pre.$$

write_manifest() {
  local notarized="$1" submission_id="$2" staple_status="$3" spctl_status="$4"
  local sha256
  sha256="$( [ -f "$ZIP_PATH" ] && shasum -a 256 "$ZIP_PATH" | awk '{print $1}' || echo null)"
  cat > "$MANIFEST_PATH" <<EOF
{
  "product": "Sigil",
  "marketing_version": "$MARKETING_VERSION",
  "build_number": "$BUILD_NUMBER",
  "bundle_id": "$ACTUAL_APP_BUNDLE_ID",
  "source": {
    "branch": "$BRANCH",
    "commit": "$SHA",
    "working_tree_dirty_allowed": $( [ "$ALLOW_DIRTY" -eq 1 ] && echo true || echo false )
  },
  "signing": {
    "identity": "$SIGNING_IDENTITY_NAME",
    "team_id": "$CODESIGN_TEAM"
  },
  "notarization": {
    "attempted": $( [ "$SKIP_NOTARIZE" -eq 0 ] && echo true || echo false ),
    "succeeded": $notarized,
    "submission_id": $( [ -n "$submission_id" ] && echo "\"$submission_id\"" || echo null ),
    "stapled": $staple_status,
    "spctl_execute_accepted": $spctl_status
  },
  "artifact": {
    "app_path": "$APP_PATH",
    "zip_path": $( [ -f "$ZIP_PATH" ] && echo "\"$ZIP_PATH\"" || echo null ),
    "zip_sha256": $( [ "$sha256" != null ] && echo "\"$sha256\"" || echo null )
  },
  "built_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
}

if [ "$SKIP_NOTARIZE" -eq 1 ]; then
  write_manifest false "" false false
  log "stopping before notarization (--skip-notarize). Build+sign+verify complete."
  log "manifest: $MANIFEST_PATH"
  log "app: $APP_PATH"
  exit 0
fi

# --- 11. Zip for submission (notarytool requires a zip/dmg/pkg, not a raw .app dir) ---
log "creating submission archive..."
ditto -c -k --keepParent "$APP_PATH" "$ZIP_PATH" || fail "ditto zip step failed"

# --- 12. Submit and wait — this is the step that talks to Apple and is
#          irreversible in the sense that it creates a real, logged
#          submission against the developer account. Only reached when
#          --skip-notarize was NOT passed. ---
log "submitting to Apple notary service and waiting for a result (this can take several minutes)..."
NOTARY_LOG="$OUT_DIR/notarytool-submit.json"
if ! xcrun notarytool submit "$ZIP_PATH" --keychain-profile "$NOTARY_PROFILE" --wait --output-format json > "$NOTARY_LOG" 2>&1; then
  cat "$NOTARY_LOG" >&2
  fail "notarytool submit failed — see $NOTARY_LOG"
fi
NOTARY_STATUS="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("status",""))' "$NOTARY_LOG")"
SUBMISSION_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("id",""))' "$NOTARY_LOG")"
[ "$NOTARY_STATUS" = "Accepted" ] || fail "notarization did not succeed: status='$NOTARY_STATUS' (submission $SUBMISSION_ID) — see $NOTARY_LOG for Apple's reasons, then fix and re-run. Nothing is stapled."
log "notarization accepted (submission $SUBMISSION_ID)"

# --- 13. Staple ---
log "stapling notarization ticket..."
xcrun stapler staple "$APP_PATH" || fail "stapler staple failed"

# --- 14. Validate the staple actually took ---
xcrun stapler validate "$APP_PATH" || fail "stapler validate failed after stapling"
log "staple validated"

# --- 15. Final Gatekeeper assessment — must now accept ---
SPCTL_OUT="$(spctl -a -vv --type execute "$APP_PATH" 2>&1)" || fail "spctl rejected the stapled app: $SPCTL_OUT"
echo "$SPCTL_OUT" | grep -q "accepted" || fail "spctl did not report 'accepted': $SPCTL_OUT"
log "Gatekeeper accepts the release: $SPCTL_OUT"

write_manifest true "$SUBMISSION_ID" true true
log "RELEASE COMPLETE"
log "app:      $APP_PATH"
log "manifest: $MANIFEST_PATH"
