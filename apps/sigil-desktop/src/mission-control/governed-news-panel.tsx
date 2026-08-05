import { useCallback, useEffect, useMemo, useRef, useState } from "react";

type JsonObject = Record<string, unknown>;

interface NewsDesktopApi {
  getGovernedNewsStatus(): Promise<JsonObject>;
  getGovernedNewsTimeline(symbol: string): Promise<JsonObject>;
  getGovernedNewsAdvisorySummary(): Promise<JsonObject>;
  collectGovernedAlpacaNews(symbols: string[]): Promise<JsonObject>;
}

function api(): NewsDesktopApi | null {
  return (
    window as Window & { sigilDesktop?: NewsDesktopApi }
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

function count(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

export function GovernedNewsPanel(): React.JSX.Element {
  const [symbolsText, setSymbolsText] = useState("");
  const [status, setStatus] = useState<JsonObject>({});
  const [advisory, setAdvisory] = useState<JsonObject>({});
  const [timeline, setTimeline] = useState<JsonObject>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("Loading governed evidence…");
  const actionLocked = useRef(false);

  const symbols = useMemo(
    () =>
      Array.from(
        new Set(
          symbolsText
            .split(/[\s,]+/)
            .map((symbol) => symbol.trim().toUpperCase())
            .filter(Boolean),
        ),
      ).slice(0, 50),
    [symbolsText],
  );

  const loadEvidence = useCallback(
    async (desktop: NewsDesktopApi): Promise<void> => {
      const [nextStatus, nextAdvisory] = await Promise.all([
        desktop.getGovernedNewsStatus(),
        desktop.getGovernedNewsAdvisorySummary(),
      ]);

      setStatus(result(nextStatus));
      setAdvisory(result(nextAdvisory));

      if (symbols[0]) {
        setTimeline(
          result(await desktop.getGovernedNewsTimeline(symbols[0])),
        );
      } else {
        setTimeline({});
      }
    },
    [symbols],
  );

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
      await loadEvidence(desktop);
      setMessage("Governed evidence refreshed.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "News refresh failed.");
    } finally {
      actionLocked.current = false;
      setBusy(false);
    }
  }, [loadEvidence]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const collect = async (): Promise<void> => {
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
      const collection = result(await desktop.collectGovernedAlpacaNews(symbols));

      if (collection.status === "disabled") {
        setMessage(
          "Alpaca collection is disabled. Set SIGIL_ALPACA_NEWS_ENABLED=true for the desktop runtime.",
        );
      } else {
        setMessage(
          collection.mode === "rolling-governed-universe"
            ? `Universe collection ${text(
                collection.status,
                "finished",
              )}: ${count(collection.processed_symbols)} of ${count(
                collection.total_symbols,
              )} symbols scanned; ${count(
                collection.stored_count,
              )} stored; next cursor ${count(collection.next_cursor)}.`
            : `Collection ${text(
                collection.status,
                "finished",
              )}: ${count(collection.stored_count)} stored, ${count(
                collection.duplicate_count,
              )} duplicates, ${count(
                collection.rejected_count,
              )} rejected.`,
        );
      }

      await loadEvidence(desktop);
    } catch (error) {
      setMessage(
        error instanceof Error ? error.message : "Alpaca collection failed.",
      );
    } finally {
      actionLocked.current = false;
      setBusy(false);
    }
  };

  const consensus = object(status.consensus);
  const timelineItems = array(timeline.headlines);
  const statusItems = array(status.headlines ?? status.items ?? status.evidence);
  const headlines = timelineItems.length ? timelineItems : statusItems;

  return (
    <section aria-label="Governed news intelligence" className="space-y-5 px-6 py-5">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-neutral-500">
            Research evidence
          </p>
          <h1 className="mt-1 text-2xl font-semibold text-neutral-100">
            Governed news intelligence
          </h1>
          <p className="mt-2 max-w-3xl text-sm text-neutral-400">
            Audited market-news research with no order or execution authority.
          </p>
        </div>
        <div className="flex flex-wrap gap-2 text-[11px] font-semibold uppercase tracking-wide">
          <span className="rounded border border-emerald-700/60 bg-emerald-950/30 px-2 py-1 text-emerald-300">
            Advisory only
          </span>
          <span className="rounded border border-emerald-700/60 bg-emerald-950/30 px-2 py-1 text-emerald-300">
            Paper only
          </span>
          <span className="rounded border border-emerald-700/60 bg-emerald-950/30 px-2 py-1 text-emerald-300">
            No broker submission
          </span>
        </div>
      </header>

      <div className="rounded-lg border border-neutral-800 bg-neutral-950/50 p-4">
        <label className="text-xs font-semibold uppercase text-neutral-400" htmlFor="news-symbols">
          Symbols
        </label>
        <div className="mt-2 flex flex-col gap-3 md:flex-row">
          <input
            className="min-w-0 flex-1 rounded border border-neutral-700 bg-neutral-900 px-3 py-2 text-sm text-neutral-100"
            id="news-symbols"
            onChange={(event) => setSymbolsText(event.target.value)}
            placeholder="Optional targeted tickers; leave blank for governed universe scan"
            value={symbolsText}
          />
          <button
            className="rounded bg-neutral-100 px-4 py-2 text-sm font-semibold text-neutral-950 disabled:opacity-50"
            disabled={busy}
            onClick={() => void collect()}
            type="button"
          >
            {symbols.length
              ? "Collect targeted news"
              : "Scan governed universe"}
          </button>
          <button
            className="rounded border border-neutral-700 px-4 py-2 text-sm font-semibold text-neutral-200 disabled:opacity-50"
            disabled={busy}
            onClick={() => void refresh()}
            type="button"
          >
            Refresh evidence
          </button>
        </div>
        <p className="mt-3 text-sm text-neutral-400" role="status">{message}</p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[
          ["Evidence", status.headline_count ?? status.evidence_count],
          ["Symbols", status.symbol_count],
          ["Corroborated", consensus.corroborated_count],
          ["Conflicting", consensus.conflicting_count],
        ].map(([label, value]) => (
          <div className="rounded-lg border border-neutral-800 bg-neutral-950/50 p-4" key={String(label)}>
            <p className="text-xs uppercase text-neutral-500">{String(label)}</p>
            <p className="mt-2 text-2xl font-semibold text-neutral-100">{count(value)}</p>
          </div>
        ))}
      </div>

      <div className="grid gap-5 xl:grid-cols-[1.6fr_1fr]">
        <div className="rounded-lg border border-neutral-800 bg-neutral-950/50">
          <div className="border-b border-neutral-800 px-4 py-3">
            <h2 className="font-semibold text-neutral-100">Evidence timeline</h2>
            <p className="mt-1 text-xs text-neutral-500">
              {text(timeline.symbol, symbols[0] ?? "Selected symbol")}
            </p>
          </div>
          <div className="divide-y divide-neutral-800">
            {headlines.length === 0 ? (
              <p className="px-4 py-8 text-sm text-neutral-500">
                No governed headlines stored yet.
              </p>
            ) : (
              headlines.slice(0, 20).map((headline, index) => (
                <article className="space-y-2 px-4 py-4" key={text(headline.evidence_identity, String(index))}>
                  <p className="text-xs text-neutral-500">
                    {text(headline.source, "Unknown source")} • {text(headline.published_at)}
                  </p>
                  <h3 className="font-medium text-neutral-100">
                    {text(headline.headline, "Untitled evidence")}
                  </h3>
                  <p className="text-sm text-neutral-400">
                    {text(headline.summary, "No summary supplied.")}
                  </p>
                </article>
              ))
            )}
          </div>
        </div>

        <aside className="rounded-lg border border-neutral-800 bg-neutral-950/50 p-4">
          <h2 className="font-semibold text-neutral-100">Advisory summary</h2>
          <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-neutral-400">
            {text(
              advisory.summary ?? advisory.advisory_summary ?? advisory.message,
              "No advisory summary is available yet.",
            )}
          </p>
          <p className="mt-5 border-t border-neutral-800 pt-4 text-xs text-neutral-500">
            Source agreement remains research evidence and cannot authorize a trade.
          </p>
        </aside>
      </div>
    </section>
  );
}
