/* Runs — what Cowork actually did, and whether anyone can corroborate it.
 *
 * The column that matters is not `outcome`, it is `confirmed`. An agent
 * reporting that it submitted a form is a claim; the confirmation text scraped
 * off the page afterwards is evidence, and this screen keeps them visibly
 * apart. NOTES.md in the loop-engineering repo records a worker that posted a
 * fabricated report including an invented exit code with zero commands run —
 * an agent that will invent a test result will invent a submission receipt.
 *
 * So a submitted run with no confirmation renders as "unverified", in the same
 * weight as a failure rather than tucked away. It is probably fine. It is not
 * evidence.
 *
 * The funnel above it comes from pipeline/outcomes.py rather than being
 * recomputed here, so the screen and the CLI cannot drift into two answers.
 * Rates are fractions, not percentages: "0/1" does not claim the precision
 * "0%" implies.
 */
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import type { Run } from "./api";
import { Empty, Panel, ServerDown } from "./components";

const STAGES = ["applied", "ack", "screening", "interview", "offer", "hired"];

function RunRow({ r }: { r: Run }) {
  const chip =
    r.outcome === "submitted"
      ? r.confirmed
        ? "chip chip--ok"
        : "chip chip--warn"
      : r.outcome === "stopped"
      ? "chip"
      : "chip chip--stop";

  const label =
    r.outcome === "submitted"
      ? r.confirmed
        ? "submitted"
        : "submitted, unverified"
      : r.outcome;

  return (
    <div className="row row--loose">
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="flex items-baseline gap-2">
          <strong className="truncate">{r.company || r.job_id}</strong>
          <span className="truncate text-muted">{r.title}</span>
        </div>

        <div className="flex flex-wrap items-center gap-2" style={{ marginTop: 6 }}>
          <span className={chip}>{label}</span>
          <span className="mono text-muted" style={{ fontSize: 11 }}>
            {r.at?.slice(0, 16).replace("T", " ")}
          </span>
          {r.blocked_by?.map((b) => (
            <span key={b} className="chip chip--warn">
              {b}
            </span>
          ))}
        </div>

        {r.confirmation && (
          <p className="cite" style={{ marginTop: 8 }}>
            {r.confirmation}
          </p>
        )}
        {r.outcome === "submitted" && !r.confirmed && (
          <p className="note note--warn" style={{ marginTop: 8 }}>
            No confirmation was captured, so nothing corroborates this beyond the
            agent's own report. Probably fine; not evidence.
          </p>
        )}
        {r.error && (
          <p className="note note--stop" style={{ marginTop: 8 }}>
            {r.error}
          </p>
        )}
      </div>
    </div>
  );
}

export function Runs() {
  const runsQ = useQuery({ queryKey: ["runs"], queryFn: api.runs });
  const outQ = useQuery({ queryKey: ["outcomes"], queryFn: api.outcomes });

  if (runsQ.isError || outQ.isError) return <ServerDown />;
  if (!runsQ.data || !outQ.data) return <Empty>Loading…</Empty>;
  const runs = runsQ.data;
  const out = outQ.data;

  const reached = out.funnel?.reached ?? {};

  return (
    <div className="flex flex-col gap-4 p-4 max-w-5xl mx-auto w-full">
      <Panel
        title="Funnel"
        note={
          out.applications === 0
            ? "nothing applied to yet"
            : `${out.applications} application${out.applications === 1 ? "" : "s"}`
        }
      >
        {out.applications === 0 ? (
          <Empty>
            The funnel needs applications in it. That is a throughput problem
            rather than a tooling one.
          </Empty>
        ) : (
          <>
            <div className="row row--base">
              {STAGES.filter((s) => reached[s]).map((s) => (
                <div key={s} style={{ marginRight: 28 }}>
                  <div className="eyebrow">{s}</div>
                  <div className="tnum" style={{ fontSize: 22, fontWeight: 600 }}>
                    {reached[s]}
                  </div>
                </div>
              ))}
            </div>

            {out.excluded_no_application > 0 && (
              <p className="note" style={{ margin: "0 var(--s5) var(--s4)" }}>
                {out.excluded_no_application} job
                {out.excluded_no_application === 1 ? "" : "s"} with stage events
                but no application on the tracker — UI tests, or withdrawn. Not
                counted.
              </p>
            )}

            {out.by_channel?.length > 0 && (
              <div className="copies">
                {out.by_channel.map((c) => (
                  <div className="row" key={`${c.applied_via}/${c.source}`}>
                    <span style={{ flex: 1 }}>
                      {c.applied_via} <span className="text-muted">via</span>{" "}
                      {c.source}
                    </span>
                    <span className="chip chip--num tnum">replied {c.rate}</span>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </Panel>

      {out.gone_quiet?.length > 0 && (
        <Panel
          title="Gone quiet"
          note={`over ${out.silence_threshold_days}d — ${out.threshold_basis}`}
        >
          <div className="copies">
            {out.gone_quiet.map((q) => (
              <div className="row" key={`${q.company}-${q.title}`}>
                <span style={{ flex: 1 }} className="truncate">
                  <strong>{q.company}</strong>{" "}
                  <span className="text-muted">{q.title}</span>
                </span>
                <span className="chip chip--warn tnum">{q.days}d</span>
              </div>
            ))}
          </div>
        </Panel>
      )}

      <Panel
        title="Cowork runs"
        note={runs.length ? `${runs.length} recorded` : undefined}
      >
        {runs.length === 0 ? (
          <Empty>
            Nothing has run yet. Cowork writes{" "}
            <code className="mono">runs/&lt;job&gt;/result.json</code>, and{" "}
            <code className="mono">python package\record_result.py</code> reads it
            back into the log.
          </Empty>
        ) : (
          <div className="copies">
            {runs.map((r) => (
              <RunRow key={r.job_id + r.at} r={r} />
            ))}
          </div>
        )}
      </Panel>
    </div>
  );
}
