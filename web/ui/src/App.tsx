/**
 * Resolve — a decision surface over the sponsor-aware job pipeline.
 *
 * Three columns: the queue of names nobody has ruled on, the decision surface
 * for the selected one, and the record of every call already made. That is the
 * whole loop on one screen, which is why there is no navigation yet.
 */
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "./api";
import type { LogEntry, ReviewItem } from "./api";
import { DecisionPanel } from "./DecisionPanel";
import { Jobs } from "./Jobs";
import { Overview } from "./Overview";
import { Queue } from "./Queue";
import { Runs } from "./Runs";
import Landing from "./landing";
import { Empty, Panel, Pill, ServerDown, Stat, ThemeToggle, useTheme } from "./components";

const qc = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } },
});

export default function App() {
  // Path-based, deliberately not a router and deliberately not mode-based.
  //
  // Routing on APP_MODE would mean waiting for /api/meta before deciding what
  // to render, which is an async decision on first paint — a flash of the wrong
  // page, every load. The path is known synchronously.
  //
  // So "/" is the landing and "/app" is the tool, in both modes. Local use goes
  // straight to work because the launcher and the installed app both open
  // "/app"; the landing is still there if you want it.
  if (window.location.pathname !== "/app") {
    return <Landing />;
  }
  return (
    <QueryClientProvider client={qc}>
      <Shell />
    </QueryClientProvider>
  );
}

function Shell() {
  const { theme, setTheme } = useTheme();
  const [selected, setSelected] = useState<string | null>(null);
  // Two screens, one piece of state. A router would buy shareable URLs for an
  // app one person opens from an icon, at the cost of another dependency.
  // Opens on the job search. The summary screen was the front door and it was
  // the wrong one: it answers "how is my search going" when the question every
  // morning is "what should I apply to". Numbers belong beside the work, not
  // in front of it.
  // "queue" is the company-name review screen and predates the Cowork work;
  // "cowork" is the prepared-application queue. Renaming the older one would
  // touch five call sites for no gain.
  const [tab, setTab] =
    useState<"overview" | "jobs" | "queue" | "cowork" | "runs">("jobs");
  const [jobFilter, setJobFilter] = useState<{ status?: string; eligible?: string }>({});

  const meta = useQuery({ queryKey: ["meta"], queryFn: api.meta });
  const review = useQuery({ queryKey: ["review"], queryFn: api.review });
  const audit = useQuery({ queryKey: ["audit"], queryFn: () => api.audit(30) });
  const verify = useQuery({ queryKey: ["verify"], queryFn: api.verify });

  const items = review.data ?? [];
  const priority = items.filter((i) => i.priority);
  // Land on a live decision rather than an empty panel. The first item is the
  // highest-value one — candidates() sorts companies with tech roles first —
  // so the loop is on screen and working before anyone clicks anything.
  const current = items.find((i) => i.key === selected) ?? items[0] ?? null;

  return (
    <div className="min-h-full flex flex-col">
      {meta.data?.mode === "demo" && <DemoBanner />}
      <header className="flex items-center justify-between gap-6 px-5 py-3"
              style={{ borderBottom: "1px solid var(--border)",
                       background: "var(--bg-raised)" }}>
        <div className="flex items-baseline gap-3">
          <h1 className="text-[15px] font-semibold tracking-tight">Get That Job</h1>
          {/* The local instance is his job site. The deployed one is a portfolio
              piece about entity resolution. Same code, and the subtitle is the
              cheapest way to stop it reading as the wrong product. */}
          <span className="text-[12px]" style={{ color: "var(--text-faint)" }}>
            {meta.data?.mode === "demo"
              ? "employer identity across four sources"
              : "jobs you can actually take, and what happened to each"}
          </span>
        </div>

        <div className="flex items-center gap-7">
          {meta.data && tab === "queue" && (
            <>
              <Stat label="Register" value={meta.data.register.entries.toLocaleString()}
                    hint={meta.data.register.route} />
              <Stat label="Resolved" value={meta.data.dataset.aliases} />
              <Stat label="Awaiting you" value={items.length}
                    hint={priority.length < items.length
                      ? `${priority.length} with tech roles` : undefined} />
            </>
          )}
          {meta.data?.mode === "demo" && <Pill tone="warn">demo data</Pill>}
          <span className="w-px h-8" style={{ background: "var(--border)" }} />
          <div className="flex rounded-lg p-0.5 gap-0.5"
               style={{ background: "var(--bg-sunken)",
                        border: "1px solid var(--border)" }}>
            {([["jobs", "Find a job"], ["cowork", "Queue"],
               ["runs", "Runs"], ["overview", "My applications"],
               ["queue", "Fix company names"]] as const)
              .map(([id, label]) => (
              <button key={id} onClick={() => setTab(id)}
                      className="px-3 py-1 text-xs rounded-md whitespace-nowrap"
                      style={tab === id
                        ? { background: "var(--bg-raised)", color: "var(--text)",
                            boxShadow: "var(--shadow)" }
                        : { color: "var(--text-faint)" }}>{label}</button>
            ))}
          </div>
          <ThemeToggle theme={theme} setTheme={setTheme} />
        </div>
      </header>

      {/* When the API is unreachable the whole page says so, once. Leaving the
          three-column layout up means two panels cheerfully invite you to
          "pick a company on the left" while the left is an error — which reads
          as three unrelated problems instead of one. */}
      {tab === "cowork" ? (
        <main className="flex-1 overflow-y-auto"><Queue /></main>
      ) : tab === "runs" ? (
        <main className="flex-1 overflow-y-auto"><Runs /></main>
      ) : tab === "overview" ? (
        <main className="flex-1"><Overview
          onOpenQueue={() => setTab("queue")}
          onOpenJobs={(f) => { setJobFilter(f); setTab("jobs"); }} /></main>
      ) : tab === "jobs" ? (
        <main className="flex-1 flex"><Jobs key={JSON.stringify(jobFilter)}
                                          initial={jobFilter} /></main>
      ) : review.isError ? (
        <main className="flex-1 p-4">
          <div className="max-w-xl mx-auto mt-12">
            <Panel title="Not connected"><ServerDown /></Panel>
          </div>
        </main>
      ) : (
      <main className="flex-1 grid gap-4 p-4"
            style={{ gridTemplateColumns: "minmax(260px,1fr) minmax(380px,1.4fr) minmax(280px,1fr)" }}>

        <Panel title="Awaiting a decision"
               right={<Pill>{items.length}</Pill>}>
          {review.isLoading && <Empty>Loading…</Empty>}
          {review.data?.length === 0 && <Empty>Queue is empty.</Empty>}
          <div className="max-h-[calc(100vh-160px)] overflow-y-auto">
            {items.map((item) => (
              <QueueRow key={item.key} item={item}
                        active={item.key === current?.key}
                        onClick={() => setSelected(item.key)} />
            ))}
          </div>
        </Panel>

        <DecisionPanel item={current} />

        <div className="flex flex-col gap-4">
          <Panel title="Decision log"
                 right={verify.data && (
                   <Pill tone={verify.data.chain_ok ? "ok" : "stop"}>
                     {verify.data.chain_ok ? "chain verified" : "chain broken"}
                   </Pill>)}>
            {audit.data?.length === 0 && (
              <Empty>Nothing recorded yet.<br />
                <span className="text-[12px]">
                  Every decision you make here lands in an append-only log.
                </span>
              </Empty>
            )}
            <div className="max-h-[50vh] overflow-y-auto">
              {(audit.data ?? []).map((e) => <AuditRow key={e.id} entry={e} />)}
            </div>
          </Panel>

          {verify.data && verify.data.unattributed_aliases.length > 0 && (
            <Panel title="Unattributed">
              <div className="px-4 py-3 text-[12px] flex flex-col gap-2">
                <p style={{ color: "var(--text-muted)" }}>
                  <strong className="tnum">
                    {verify.data.unattributed_aliases.length}
                  </strong>{" "}
                  aliases were confirmed on the command line before this log
                  existed. Who decided them, and why, is genuinely unknown — so
                  it is reported rather than filled in.
                </p>
                <div className="flex flex-wrap gap-1">
                  {verify.data.unattributed_aliases.slice(0, 12).map((a) => (
                    <Pill key={a}>{a}</Pill>
                  ))}
                </div>
              </div>
            </Panel>
          )}
        </div>
      </main>
      )}
    </div>
  );
}

function QueueRow({ item, active, onClick }: {
  item: ReviewItem; active: boolean; onClick: () => void;
}) {
  return (
    <button onClick={onClick}
            className="w-full text-left px-4 py-2.5 transition-colors"
            style={{
              background: active ? "var(--accent-soft)" : "transparent",
              borderBottom: "1px solid var(--border)",
              borderLeft: `2px solid ${active ? "var(--accent)" : "transparent"}`,
            }}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-[13px] font-medium truncate">{item.company}</span>
        <span className="flex gap-1 shrink-0">
          {item.priority && <Pill tone="accent">tech</Pill>}
          <Pill>{item.rows}</Pill>
        </span>
      </div>
      {item.tech_titles[0] && (
        <div className="text-[11px] truncate mt-0.5"
             style={{ color: "var(--text-faint)" }}>
          {item.tech_titles[0]}
        </div>
      )}
    </button>
  );
}

function AuditRow({ entry }: { entry: LogEntry }) {
  const changed = (entry.effects as any)?.rows_changed ?? 0;
  return (
    <div className="px-4 py-2.5"
         style={{ borderBottom: "1px solid var(--border)" }}>
      <div className="flex items-baseline justify-between gap-2">
        <span className="mono text-[11.5px]" style={{ color: "var(--accent)" }}>
          {entry.type}
        </span>
        <span className="mono text-[10.5px]" style={{ color: "var(--text-faint)" }}>
          {entry.ts.replace("T", " ").replace("Z", "")}
        </span>
      </div>
      <div className="text-[12.5px] mt-0.5">
        <span className="mono">{String(entry.entity.id)}</span>
        {changed > 0 && (
          <span style={{ color: "var(--text-muted)" }}>
            {" "}· {changed} row{changed === 1 ? "" : "s"}
          </span>
        )}
      </div>
      {entry.rationale && (
        <div className="text-[11.5px] mt-1 italic"
             style={{ color: "var(--text-muted)" }}>
          “{entry.rationale}”
        </div>
      )}
      <div className="text-[10.5px] mt-1" style={{ color: "var(--text-faint)" }}>
        {entry.actor.display_name}
      </div>
    </div>
  );
}

/**
 * Said plainly and unmissably, because this page is public and every company
 * name on it is invented. A visitor must never be able to read a verdict here
 * as information about a real employer.
 */
function DemoBanner() {
  const [open, setOpen] = useState(false);
  return (
    <div className="px-5 py-2 text-[12px]"
         style={{ background: "var(--warn-soft)", color: "var(--warn)",
                  borderBottom: "1px solid var(--border)" }}>
      <div className="flex items-center justify-between gap-4">
        <span>
          <strong>Synthetic data.</strong> These companies do not exist and this
          is not the Home Office register. Your decisions are real, shared with
          everyone else here, and cleared whenever the demo restarts.
        </span>
        <button onClick={() => setOpen(!open)}
                className="underline underline-offset-2 shrink-0">
          {open ? "hide" : "what is this?"}
        </button>
      </div>
      {open && (
        <div className="mt-2 pt-2 flex flex-col gap-1.5 max-w-3xl"
             style={{ borderTop: "1px solid var(--warn)" }}>
          <p>
            A UK Skilled Worker visa makes a job you cannot legally take worse
            than no job at all, so every posting has to be checked against the
            Home Office register of licensed sponsors. That sounds like a join
            and is not: the job board prints the brand, the register records the
            legal entity. Monzo is filed as MONZO BANK, Deliveroo as ROOFOODS
            LTD T A DELIVEROO.
          </p>
          <p>
            Two deterministic rules propose candidates. They never decide. A
            human confirms, the choice is validated against the register,
            written back to the store, and recorded with a reason and a name.
            The suggester deliberately offers obvious rubbish alongside the real
            answer, because every false positive costs a human decision and
            precision matters more than recall here.
          </p>
          <p>
            <strong>Attribution is not authentication.</strong> There is no login
            here. A name on a decision identifies, it does not verify — in a real
            deployment the actor would be the SSO subject.
          </p>
        </div>
      )}
    </div>
  );
}
