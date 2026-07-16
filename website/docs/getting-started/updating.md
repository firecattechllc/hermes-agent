---
sidebar_position: 3
title: "Updating & Uninstalling"
description: "Update, roll back, adopt, eject, or uninstall Hermes"
---

# Updating & Uninstalling

## Updating

```bash
hermes update
```

Hermes routes this command according to how it was installed. It never mutates a package-manager install behind that package manager's back.

| Install type | What `hermes update` does |
|---|---|
| **Managed bundle** (the standard installer default) | Downloads the latest signed release, verifies every file, stages and preflights an immutable slot, atomically flips `current.txt`, restages the updater, reapplies lazy features, and requests a gateway restart. |
| **Source checkout** (`install.sh --source`, `install.ps1 -Source`, or `hermes eject`) | Fast-forwards a clean checkout. For local changes, offers a new worktree (default), an ordinary Git merge, or cancel. It never auto-stashes or mutates the active venv in place. |
| **Nix, Homebrew, pip, or Docker** | Refuses with the package-manager/image command you should use instead. |

Your configuration, credentials, sessions, skills, and other durable state remain under `HERMES_HOME`; release slots live under `$HERMES_HOME/versions/`.

### Managed release status

```bash
hermes-updater status
hermes-updater status --check
hermes-updater status --check --json
```

Status reports the current and previous slots, channel, interrupted staging leftovers, latest available release, releases behind, release notes, and build SHAs. A network failure is reported without modifying the active slot.

### Atomic update and rollback

A managed update follows this order:

1. resolve and stream the platform archive;
2. require its Ed25519 signature and verify every manifest hash;
3. unpack into `versions/<version>.staging`;
4. run the staged slot's `hermes doctor --preflight`;
5. rename staging to an immutable slot and atomically replace `current.txt`;
6. restage the stable launcher/updater, reapply the feature ledger, and restart services.

Any failure before step 5 deletes staging and leaves the current version untouched. To switch instantly to the prior slot:

```bash
hermes-updater rollback
```

Running processes keep using the concrete old slot they started from until they restart.

### Source checkouts and worktrees

Inside a Hermes source checkout, say which runtime you mean:

```bash
hermes --dev --version      # this checkout
hermes --global --version   # installed/managed Hermes
```

Plain `hermes` refuses inside a checkout to prevent accidental environment skew. Provision a checkout with:

```bash
hermes dev sync
hermes dev sync --watch --only tui web
```

If `hermes update` finds local edits, the default **Switch** option creates `.worktrees/main-<sha>`, provisions it, and repoints the command link while preserving the original checkout byte-for-byte. Remove inactive update worktrees with `hermes dev gc`; the active target is never removed.

### Move between managed and source worlds

A pristine legacy checkout can adopt managed releases automatically or on prompt, according to `updates.adopt: auto|prompt|never`:

```bash
hermes adopt
hermes-updater adopt --undo
```

Adoption keeps the checkout untouched and records the old command target for undo. To move a managed install back to a development checkout:

```bash
hermes eject
```

`eject` clones the exact source revision recorded by the active slot, runs `hermes dev sync`, and repoints the command link. Your `HERMES_HOME` data is shared across the transition.

### Updating from messaging platforms

Send `/update`. The gateway invokes the same updater, drains active work when supported, and restarts on the new slot. The update marker exists only during the short flip/restart critical section.

### Package-managed installs

Use the owner of the installation:

```bash
# Nix
nix profile upgrade hermes-agent
nix profile rollback

# Homebrew
brew upgrade hermes-agent

# pip (legacy/manual)
pip install --upgrade hermes-agent

# Docker
# pull and recreate from the new image; do not update inside the container
docker pull nousresearch/hermes-agent:latest
```

## Uninstalling

```bash
hermes uninstall
```

The uninstaller can preserve `HERMES_HOME` for a future reinstall. Stop an installed gateway service first if requested. Package-managed installations should be removed through their package manager.

For manual cleanup, remove the command link and installation tree. Delete `~/.hermes` only if you intentionally want to erase configuration, credentials, sessions, skills, and all managed slots.
