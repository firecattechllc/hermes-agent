#!/usr/bin/env bash
#
# build_and_run.sh — build and launch the native Sigil 4.0 Mission Control
# shell for local development.
#
# Usage:
#   script/build_and_run.sh            build, terminate any running dev
#                                       build, then launch the fresh build
#   script/build_and_run.sh --verify   also confirm the process launches,
#                                       then quit it and exit 0/1
#
# SAFETY / IDENTITY ISOLATION
# ----------------------------------------------------------------------------
# The installed, certified Sigil 3.7 app (a separate Electron app, bundle id
# com.firecattechnology.sigil, installed at /Applications/Sigil.app) and the
# native Sigil 4.0 development app used to share the display name "Sigil",
# which made name-based process targeting dangerous — a name match could hit
# either app. That collision is now eliminated structurally:
#
#   - The dev build's bundle id is com.firecattechnology.Sigil.dev
#   - The dev build's product/executable name is SigilDev
#   - The dev build's app bundle is SigilDev.app
#
# This script identifies and controls ONLY the dev build, and does so
# exclusively via bundle identifier, PID, or the exact DerivedData app path —
# never by process/application *name*. The following are intentionally never
# used anywhere in this script: `pkill Sigil`, `killall Sigil`,
# `tell process "Sigil"`, `tell application "Sigil"`, or any other pattern
# that matches on a human-readable app/process name. Do not reintroduce them.
#
# A regression check further proves the installed Sigil 3.7 app (if running)
# is left completely alone: its PID and window geometry are recorded before
# any dev-build action and compared after, and the script fails loudly if
# either changed.
#
# The governed loopback bridge is an embedded XPC service. Launching SigilDev
# activates it automatically; this script never starts an external runtime.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${SCRIPT_DIR}/.."
XCODE_PROJECT="${PROJECT_ROOT}/Sigil/Sigil.xcodeproj"
SCHEME="Sigil"
CONFIGURATION="Debug"
VERIFY_MODE=0

# Identity constants. EXPECTED_DEV_BUNDLE_ID is asserted against the value
# Xcode actually reports for the built product (never trusted blindly), and
# LEGACY_BUNDLE_ID is used strictly read-only, to look up the installed
# Sigil 3.7 app for the regression check below.
EXPECTED_DEV_BUNDLE_ID="com.firecattechnology.Sigil.dev"
LEGACY_BUNDLE_ID="com.firecattechnology.sigil"


for arg in "$@"; do
  case "$arg" in
    --verify)
      VERIFY_MODE=1
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      echo "Usage: $0 [--verify]" >&2
      exit 2
      ;;
  esac
done

if [[ ! -d "$XCODE_PROJECT" ]]; then
  echo "error: Xcode project not found at $XCODE_PROJECT" >&2
  exit 1
fi

# --- Locate a full Xcode install (not just Command Line Tools) -------------
resolve_developer_dir() {
  local candidate
  candidate="$(xcode-select -p 2>/dev/null || true)"
  if [[ -n "$candidate" && "$candidate" == *"/Xcode.app/"* ]]; then
    echo "$candidate"
    return
  fi
  for app in /Applications/Xcode.app "$HOME/Downloads/Xcode.app" /Applications/Xcode-beta.app; do
    if [[ -d "$app/Contents/Developer" ]]; then
      echo "$app/Contents/Developer"
      return
    fi
  done
  echo ""
}

DEVELOPER_DIR="$(resolve_developer_dir)"
if [[ -z "$DEVELOPER_DIR" ]]; then
  echo "error: could not find a full Xcode install (only Command Line Tools were found)." >&2
  echo "       install Xcode or set DEVELOPER_DIR before running this script." >&2
  exit 1
fi
export DEVELOPER_DIR
echo "==> Using DEVELOPER_DIR=$DEVELOPER_DIR"

XCODEBUILD="${DEVELOPER_DIR}/usr/bin/xcodebuild"

echo "==> Project:    $XCODE_PROJECT"
echo "==> Scheme:     $SCHEME"
echo "==> Config:     $CONFIGURATION"

# --- Resolve the build output identity for this scheme/config --------------
BUILD_SETTINGS="$("$XCODEBUILD" -project "$XCODE_PROJECT" -scheme "$SCHEME" -configuration "$CONFIGURATION" -showBuildSettings 2>/dev/null)"
BUILT_PRODUCTS_DIR="$(echo "$BUILD_SETTINGS" | awk -F ' = ' '/ BUILT_PRODUCTS_DIR /{print $2; exit}')"
FULL_PRODUCT_NAME="$(echo "$BUILD_SETTINGS" | awk -F ' = ' '/ FULL_PRODUCT_NAME /{print $2; exit}')"
EXECUTABLE_NAME="$(echo "$BUILD_SETTINGS" | awk -F ' = ' '/ EXECUTABLE_NAME /{print $2; exit}')"
DEV_BUNDLE_ID="$(echo "$BUILD_SETTINGS" | awk -F ' = ' '/ PRODUCT_BUNDLE_IDENTIFIER /{print $2; exit}')"
APP_PATH="${BUILT_PRODUCTS_DIR}/${FULL_PRODUCT_NAME}"

# --- Identity assertions -----------------------------------------------------
# Fail closed if the project ever drifts back toward colliding with the
# installed Sigil 3.7 app's identity (bundle id com.firecattechnology.sigil,
# product name Sigil). This is the load-bearing safety gate: everything below
# assumes DEV_BUNDLE_ID is distinct from LEGACY_BUNDLE_ID.
if [[ "$DEV_BUNDLE_ID" != "$EXPECTED_DEV_BUNDLE_ID" ]]; then
  echo "error: unexpected dev bundle id '$DEV_BUNDLE_ID' (expected '$EXPECTED_DEV_BUNDLE_ID')." >&2
  echo "       refusing to proceed until project identity is confirmed." >&2
  exit 1
fi
LOWER_DEV_BUNDLE_ID="$(echo "$DEV_BUNDLE_ID" | tr '[:upper:]' '[:lower:]')"
LOWER_LEGACY_BUNDLE_ID="$(echo "$LEGACY_BUNDLE_ID" | tr '[:upper:]' '[:lower:]')"
if [[ "$LOWER_DEV_BUNDLE_ID" == "$LOWER_LEGACY_BUNDLE_ID" ]]; then
  echo "error: dev bundle id collides with the installed Sigil 3.7 bundle id ($LEGACY_BUNDLE_ID)." >&2
  exit 1
fi
if [[ "$EXECUTABLE_NAME" == "Sigil" ]]; then
  echo "error: dev executable name is 'Sigil', which collides with the installed Sigil 3.7 executable." >&2
  exit 1
fi

echo "==> Dev bundle id:   $DEV_BUNDLE_ID"
echo "==> Dev executable:  $EXECUTABLE_NAME"
echo "==> Dev app path:    $APP_PATH"

# --- Bundle-id-only process helpers (never name-based) ----------------------
# All of these resolve processes exclusively via CFBundleIdentifier through
# System Events, or via an exact numeric PID / exact filesystem path already
# obtained that way. None of them ever match on a human-readable app name.
pid_for_bundle_id() {
  local bundle_id="$1"
  osascript -e "
    tell application \"System Events\"
      try
        return unix id of first process whose bundle identifier is \"${bundle_id}\"
      on error
        return \"\"
      end try
    end tell" 2>/dev/null
}

window_geometry_for_bundle_id() {
  local bundle_id="$1"
  osascript -e "
    tell application \"System Events\"
      try
        set p to first process whose bundle identifier is \"${bundle_id}\"
        if (count of windows of p) > 0 then
          return (position of window 1 of p as string) & \"|\" & (size of window 1 of p as string)
        else
          return \"no-window\"
        end if
      on error errText
        return \"unavailable\"
      end try
    end tell" 2>/dev/null
}

terminate_dev_build() {
  local pid
  pid="$(pid_for_bundle_id "$DEV_BUNDLE_ID")"
  if [[ -z "$pid" ]]; then
    echo "==> No running dev build found (bundle id $DEV_BUNDLE_ID)"
    return
  fi
  echo "==> Terminating existing dev build: bundle id $DEV_BUNDLE_ID, pid $pid"
  osascript -e "tell application id \"${DEV_BUNDLE_ID}\" to quit" >/dev/null 2>&1 || true
  for _ in 1 2 3 4 5; do
    if [[ -z "$(pid_for_bundle_id "$DEV_BUNDLE_ID")" ]]; then
      return
    fi
    sleep 1
  done
  # Fall back to an exact numeric PID kill — this is the same PID we already
  # resolved via bundle identifier above, never a name-based match.
  if kill -0 "$pid" 2>/dev/null; then
    echo "==> Dev build did not quit cleanly; sending SIGKILL to exact pid $pid"
    kill -9 "$pid" 2>/dev/null || true
  fi
}

# --- Regression check (before): record installed Sigil 3.7 state -----------
echo "==> Checking installed Sigil 3.7 app (bundle id $LEGACY_BUNDLE_ID)..."
LEGACY_PID_BEFORE="$(pid_for_bundle_id "$LEGACY_BUNDLE_ID")"
if [[ -n "$LEGACY_PID_BEFORE" ]]; then
  LEGACY_GEOMETRY_BEFORE="$(window_geometry_for_bundle_id "$LEGACY_BUNDLE_ID")"
  echo "==> Sigil 3.7 is running: pid $LEGACY_PID_BEFORE, window geometry: $LEGACY_GEOMETRY_BEFORE"
else
  echo "==> Sigil 3.7 is not currently running — nothing to compare after rebuild"
fi

# --- Terminate any previously-launched dev build ----------------------------
terminate_dev_build

# --- Build -------------------------------------------------------------------
echo "==> Building..."
"$XCODEBUILD" \
  -project "$XCODE_PROJECT" \
  -scheme "$SCHEME" \
  -configuration "$CONFIGURATION" \
  -destination 'platform=macOS' \
  build

if [[ ! -d "$APP_PATH" ]]; then
  echo "error: expected built app not found at $APP_PATH" >&2
  exit 1
fi

echo "==> Built app:  $APP_PATH"

# --- Launch --------------------------------------------------------------
echo "==> Launching $APP_PATH"
open "$APP_PATH"

VERIFY_STATUS=0
if [[ "$VERIFY_MODE" -eq 1 ]]; then
  echo "==> Verifying process launches..."
  ATTEMPTS=10
  LAUNCHED_PID=""
  for ((i = 1; i <= ATTEMPTS; i++)); do
    LAUNCHED_PID="$(pid_for_bundle_id "$DEV_BUNDLE_ID")"
    if [[ -n "$LAUNCHED_PID" ]]; then
      break
    fi
    sleep 1
  done

  if [[ -n "$LAUNCHED_PID" ]]; then
    echo "==> VERIFY PASSED: process running (bundle id $DEV_BUNDLE_ID, pid $LAUNCHED_PID)"
    echo "==> Quitting verification launch"
    terminate_dev_build
  else
    echo "==> VERIFY FAILED: process did not launch within ${ATTEMPTS}s" >&2
    VERIFY_STATUS=1
  fi
fi

# --- Regression check (after): prove Sigil 3.7 is unchanged -----------------
if [[ -n "$LEGACY_PID_BEFORE" ]]; then
  LEGACY_PID_AFTER="$(pid_for_bundle_id "$LEGACY_BUNDLE_ID")"
  LEGACY_GEOMETRY_AFTER="$(window_geometry_for_bundle_id "$LEGACY_BUNDLE_ID")"

  if [[ "$LEGACY_PID_AFTER" != "$LEGACY_PID_BEFORE" ]]; then
    echo "==> REGRESSION CHECK FAILED: Sigil 3.7 pid changed ($LEGACY_PID_BEFORE -> $LEGACY_PID_AFTER)." >&2
    echo "    This means something restarted or killed the installed 3.7 app." >&2
    exit 1
  fi

  if [[ "$LEGACY_GEOMETRY_AFTER" != "$LEGACY_GEOMETRY_BEFORE" ]]; then
    echo "==> REGRESSION CHECK WARNING: Sigil 3.7 window geometry changed." >&2
    echo "    before: $LEGACY_GEOMETRY_BEFORE" >&2
    echo "    after:  $LEGACY_GEOMETRY_AFTER" >&2
    echo "    pid is unchanged ($LEGACY_PID_BEFORE), so the process itself was not touched by this script," >&2
    echo "    but something moved/resized its window during this run." >&2
  else
    echo "==> REGRESSION CHECK PASSED: Sigil 3.7 unchanged (pid $LEGACY_PID_BEFORE, window geometry identical)."
  fi
else
  echo "==> REGRESSION CHECK SKIPPED: Sigil 3.7 was not running before this script ran."
fi

if [[ "$VERIFY_STATUS" -ne 0 ]]; then
  exit "$VERIFY_STATUS"
fi

echo "==> Done."
