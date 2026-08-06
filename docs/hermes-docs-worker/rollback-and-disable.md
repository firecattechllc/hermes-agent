# Rollback / Disable Procedure

## Immediate stop (no uninstall)

```
sudo systemctl stop hermes-docs-evidence.timer hermes-docs-daily.timer hermes-docs-weekly.timer
sudo systemctl disable hermes-docs-evidence.timer hermes-docs-daily.timer hermes-docs-weekly.timer
```

No in-flight run is interrupted destructively: `Type=oneshot` services run
to completion or fail on their own; there is no partial-write state to
clean up because the worker writes files, then commits, then pushes, then
opens a PR — each step is independently safe to have stopped after (an
uncommitted write, an unpushed commit, and a not-yet-opened PR are all
inert). If a run is in progress when you stop the timers, let it finish or
`systemctl kill` the running `.service` unit; the next run (if you re-enable
later) will simply re-derive fresh evidence.

## Remove an in-flight lock (only if a run crashed uncleanly)

```
sudo -u hermes-docs rm -f /var/lib/hermes-docs-worker/run.lock
```

Only do this if you've confirmed via `systemctl status` /
`ps` that no worker process is actually running — the lock is an `flock`,
which the OS releases automatically on process exit, so this should not
normally be necessary.

## Undo an already-opened, unmerged PR

Close it on GitHub. The worker takes no action in response either way; it
will simply stop seeing an open PR on its next run (once you've also
stopped or fixed whatever triggered it) and may open a new one.

## Full uninstall

```
sudo systemctl disable --now hermes-docs-evidence.timer hermes-docs-daily.timer hermes-docs-weekly.timer
sudo rm /etc/systemd/system/hermes-docs-*.service /etc/systemd/system/hermes-docs-*.timer
sudo systemctl daemon-reload

sudo rm -rf /var/lib/hermes-docs-worker
sudo rm /etc/hermes/docs-worker.env

# Leave /opt/hermes-docs/hydra-docs in place unless you specifically want
# to remove the vault checkout too -- it is the canonical GitHub repo's
# local clone, not worker-owned state.

sudo userdel hermes-docs
```

## Reverting a merged automation PR

If a merged PR turns out to contain something wrong, revert it exactly like
any other commit to `main`:

```
git revert <merge-commit-sha>
```

The worker has no special awareness of this; its next run will simply
observe the current (reverted) state of `main` and generate fresh evidence
against it.

## Downgrading / disabling just one schedule

Each of the three timers is independent. To keep hourly evidence collection
but stop the daily narrative report, for example:

```
sudo systemctl disable --now hermes-docs-daily.timer
```

`hermes-docs-evidence.timer` and `hermes-docs-weekly.timer` are unaffected.
