import { useCallback, useEffect, useRef, useState } from "react";

type JsonObject = Record<string, unknown>;

interface VisibilityDesktopApi {
  getComputerUseVisibility(): Promise<JsonObject>;
  getHermesWebUIStatus(): Promise<JsonObject>;
  getPaperclipStatus(): Promise<JsonObject>;
}

function api(): VisibilityDesktopApi | null {
  return (
    window as Window & { sigilDesktop?: VisibilityDesktopApi }
  ).sigilDesktop ?? null;
}

function object(value: unknown): JsonObject {
  return typeof value === "object" && value !== null
    ? (value as JsonObject)
    : {};
}

function result(value: unknown): JsonObject {
  const response = object(value);

  return response.ok === true ? object(response.result) : response;
}

function array(value: unknown): JsonObject[] {
  return Array.isArray(value) ? value.map(object) : [];
}

function text(value: unknown, fallback = "—"): string {
  return typeof value === "string" && value.trim() ? value : fallback;
}

const STATE_TONE: Record<string, string> = {
  healthy: "text-emerald-400",
  degraded: "text-amber-400",
  disabled: "text-neutral-500",
  stale: "text-amber-400",
  unavailable: "text-neutral-500",
  incompatible: "text-red-400",
};

export function GovernedVisibilityPanel(): React.JSX.Element {
  const [computerUse, setComputerUse] = useState<JsonObject>({});
  const [webui, setWebui] = useState<JsonObject>({});
  const [paperclip, setPaperclip] = useState<JsonObject>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("Loading visibility evidence…");
  const actionLocked = useRef(false);

  const refresh = useCallback(async () => {
    if (actionLocked.current) {
      return;
    }

    const desktop = api();

    if (!desktop) {
      setMessage("Desktop bridge unavailable.");

      return;
    }

    actionLocked.current = true;
    setBusy(true);

    try {
      const [nextComputerUse, nextWebui, nextPaperclip] = await Promise.all([
        desktop.getComputerUseVisibility(),
        desktop.getHermesWebUIStatus(),
        desktop.getPaperclipStatus(),
      ]);

      setComputerUse(result(nextComputerUse));
      setWebui(result(nextWebui));
      setPaperclip(result(nextPaperclip));
      setMessage("Visibility evidence refreshed.");
    } catch (error) {
      setMessage(
        error instanceof Error ? error.message : "Visibility refresh failed.",
      );
    } finally {
      actionLocked.current = false;
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const rawComputerUse = object(computerUse.raw);
  const targets = array(webui.targets);

  return (
    <section
      aria-label="Governed ecosystem visibility"
      className="space-y-5 px-6 py-5"
    >
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-neutral-500">
            Read-only evidence
          </p>
          <h1 className="mt-1 text-2xl font-semibold text-neutral-100">
            Computer use &amp; Hermes WebUI visibility
          </h1>
          <p className="mt-2 max-w-3xl text-sm text-neutral-400">
            Status surfaced from the authoritative systems only —{" "}
            <code>tools/computer_use/</code> and{" "}
            <code>sigil.hermes_webui_adapter</code>. No action is taken by
            this panel.
          </p>
        </div>
        <button
          className="rounded border border-neutral-700 px-4 py-2 text-sm font-semibold text-neutral-200 disabled:opacity-50"
          disabled={busy}
          onClick={() => void refresh()}
          type="button"
        >
          Refresh evidence
        </button>
      </header>

      <p className="text-sm text-neutral-400" role="status">
        {message}
      </p>

      <div className="grid gap-5 xl:grid-cols-2">
        <div className="rounded-lg border border-neutral-800 bg-neutral-950/50 p-4">
          <h2 className="font-semibold text-neutral-100">
            Governed computer use
          </h2>
          <p className="mt-1 text-xs text-neutral-500">
            Every destructive action still requires the existing approval
            callback; this panel only reads driver readiness.
          </p>
          <dl className="mt-4 grid gap-3 sm:grid-cols-2">
            <div>
              <dt className="text-xs uppercase text-neutral-500">
                Driver ready
              </dt>
              <dd className="mt-1 text-sm font-semibold text-neutral-100">
                {computerUse.driverReady || computerUse.driver_ready
                  ? "Yes"
                  : "No"}
              </dd>
            </div>
            <div>
              <dt className="text-xs uppercase text-neutral-500">
                Execution requires approval
              </dt>
              <dd className="mt-1 text-sm font-semibold text-emerald-400">
                Always
              </dd>
            </div>
            <div>
              <dt className="text-xs uppercase text-neutral-500">Platform</dt>
              <dd className="mt-1 text-sm text-neutral-300">
                {text(rawComputerUse.platform)}
              </dd>
            </div>
            <div>
              <dt className="text-xs uppercase text-neutral-500">
                Driver version
              </dt>
              <dd className="mt-1 text-sm text-neutral-300">
                {text(rawComputerUse.version)}
              </dd>
            </div>
          </dl>
          {!computerUse.available ? (
            <p className="mt-3 text-xs text-amber-400">
              {text(computerUse.reason, "Probe unavailable.")}
            </p>
          ) : null}
        </div>

        <div className="rounded-lg border border-neutral-800 bg-neutral-950/50 p-4">
          <h2 className="font-semibold text-neutral-100">Hermes WebUI</h2>
          <p className="mt-1 text-xs text-neutral-500">
            Disabled by policy unless a target has been explicitly enabled by
            operator configuration.
          </p>
          <div className="mt-4 space-y-3">
            {targets.length === 0 ? (
              <p className="text-sm text-neutral-500">No targets configured.</p>
            ) : (
              targets.map((target) => (
                <div
                  className="rounded border border-neutral-800 px-3 py-2"
                  key={text(target.node_id, String(Math.random()))}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-medium text-neutral-100">
                      {text(target.display_name)}
                    </span>
                    <span
                      className={
                        STATE_TONE[String(target.state ?? "")] ??
                        "text-neutral-400"
                      }
                    >
                      {text(target.state).toUpperCase()}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-neutral-500">
                    {text(target.reason)}
                  </p>
                </div>
              ))
            )}
          </div>
        </div>

        <div className="rounded-lg border border-neutral-800 bg-neutral-950/50 p-4">
          <h2 className="font-semibold text-neutral-100">Paperclip</h2>
          <p className="mt-1 text-xs text-neutral-500">
            Read-only identity check against an operator-configured instance.
            Disabled by policy unless <code>SIGIL_PAPERCLIP_ENABLED=true</code>.
          </p>
          <dl className="mt-4 grid gap-3 sm:grid-cols-2">
            <div>
              <dt className="text-xs uppercase text-neutral-500">Configured</dt>
              <dd className="mt-1 text-sm font-semibold text-neutral-100">
                {paperclip.configured ? "Yes" : "No"}
              </dd>
            </div>
            <div>
              <dt className="text-xs uppercase text-neutral-500">Connected</dt>
              <dd
                className={
                  paperclip.connected
                    ? "mt-1 text-sm font-semibold text-emerald-400"
                    : "mt-1 text-sm font-semibold text-neutral-400"
                }
              >
                {paperclip.connected ? "Yes" : "No"}
              </dd>
            </div>
          </dl>
          <p className="mt-3 text-xs text-neutral-500">
            {text(paperclip.reason, text(paperclip.agent_name, "—"))}
          </p>
        </div>
      </div>
    </section>
  );
}
