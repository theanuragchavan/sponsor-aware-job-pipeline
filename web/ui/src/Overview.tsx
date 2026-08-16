/**
 * The screen you open in the morning.
 *
 * Every label here is written for someone who has not read the code. Not
 * "eligible", "actioned" or "review queue" — "you can apply to these", "you
 * applied", "company names to check". The filtering underneath is genuinely
 * subtle and the words on the screen should not be.
 *
 * Three sections, in the order the day runs: what to do now, what you already
 * did, and the small chore that unblocks more of the first.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, api } from "./api";
import type { Job } from "./api";
import { Button, Empty, Panel, Pill, ServerDown } from "./components";

export function Overview({ onOpenQueue, onOpenJobs }: {
  onOpenQueue: () => void;
  onOpenJobs: (filter: { status?: string; eligible?: string }) => void;
}) {
  const summary = useQuery({ queryKey: ["summary"], queryFn: api.summary });
  const ready = useQuery({
    queryKey: ["jobs", "ready"],
    queryFn: () => api.jobs("eligible=true&status=new&limit=25&sort=score"),
  });
  const applied = useQuery({
    queryKey: ["jobs", "applied"],
    queryFn: () => api.jobs("status=applied&limit=25"),
  });

  if (summary.isError) {
    return <div className="p-4 max-w-xl mx-auto mt-12">
      <Panel title="Not connected"><ServerDown /></Panel>
    </div>;
  }

  const s = summary.data;

  return (
    <div className="flex flex-col gap-4 p-4 max-w-5xl mx-auto w-full">

      {/* --- the numbers ---------------------------------------------------- */}
      <div className="grid gap-3"
           style={{ gridTemplateColumns: "repeat(auto-fit,minmax(150px,1fr))" }}>
        <Figure label="You can apply to" value={s?.ready_to_apply}
                tone="accent" hint="passed every check"
                onClick={() => onOpenJobs({ eligible: "true", status: "new" })} />
        <Figure label="You've applied to" value={s?.applied}
                hint={s ? `${s.awaiting_reply} waiting to hear back` : ""}
                onClick={() => onOpenJobs({ status: "applied", eligible: "" })} />
        <Figure label="Names to check" value={s?.needs_a_name_decision}
                hint="unlocks more jobs" onClick={onOpenQueue} />
        <Figure label="Jobs tracked" value={s?.tracked}
                hint={s ? `${s.filtered_out.toLocaleString()} filtered out` : ""}
                onClick={() => onOpenJobs({ eligible: "", status: "" })} />
      </div>

      {s && s.filtered_no_sponsor > 0 && (
        <p className="text-[12px] px-1" style={{ color: "var(--text-faint)" }}>
          Of the {s.filtered_out.toLocaleString()} filtered out,{" "}
          <strong>{s.filtered_no_sponsor.toLocaleString()}</strong> were dropped
          because the employer is not on the sponsor register — you could not
          legally take them. The rest were too senior, internships, or needed
          security clearance.
        </p>
      )}

      {/* --- what to do now ------------------------------------------------- */}
      <Panel title="Apply to these"
             right={<Pill tone="accent">{ready.data?.total ?? 0}</Pill>}>
        {ready.isLoading && <Empty>Loading…</Empty>}
        {ready.data?.items.length === 0 && (
          <Empty>Nothing left in the queue. Either you have applied to
            everything, or check the names below to unlock more.</Empty>
        )}
        {ready.data?.items.map((job) => <JobRow key={job.id} job={job} />)}
      </Panel>

      {/* --- what you already did ------------------------------------------- */}
      <Panel title="You've applied to these"
             right={<Pill>{applied.data?.total ?? 0}</Pill>}>
        {applied.data?.items.length === 0 && (
          <Empty>Nothing yet. Mark one above when you send it.</Empty>
        )}
        {applied.data?.items.map((job) => (
          <div key={job.id} className="px-4 py-2.5 flex items-baseline gap-3"
               style={{ borderBottom: "1px solid var(--border)" }}>
            <span className="text-[13px] font-medium">{job.company}</span>
            <span className="text-[12.5px] flex-1 truncate"
                  style={{ color: "var(--text-muted)" }}>{job.title}</span>
            <span className="text-[11.5px] mono shrink-0"
                  style={{ color: "var(--text-faint)" }}>
              {job.date_applied} · {job.applied_via}
            </span>
          </div>
        ))}
      </Panel>

      {/* --- the chore that unblocks the first section ----------------------- */}
      <Panel title="Company names to check"
             right={<Button onClick={onOpenQueue}>Open</Button>}>
        <div className="px-4 py-3 text-[12.5px]"
             style={{ color: "var(--text-muted)" }}>
          <strong className="tnum" style={{ color: "var(--text)" }}>
            {s?.needs_a_name_decision ?? 0}
          </strong>{" "}
          companies where the job board and the government register spell the
          name differently, so the computer cannot tell whether they can sponsor
          you. Each one you confirm can put several more jobs into the list above.
        </div>
      </Panel>
    </div>
  );
}

/**
 * A number, and the list behind it.
 *
 * These read as buttons — a big figure in a bordered card — so they have to
 * behave like buttons. Rendering them as inert divs was a small lie the eye
 * tells you, and the first thing anyone does with a dashboard tile is click it.
 */
function Figure({ label, value, hint, tone, onClick }: {
  label: string; value?: number; hint?: string; tone?: "accent";
  onClick?: () => void;
}) {
  return (
    <button onClick={onClick} disabled={!onClick}
            className="rounded-xl px-4 py-3 text-left transition-colors
                       disabled:cursor-default hover:brightness-110"
            style={{ background: "var(--bg-raised)",
                     border: `1px solid ${tone === "accent" ? "var(--accent)" : "var(--border)"}` }}>
      <div className="text-[11px] uppercase tracking-wider"
           style={{ color: "var(--text-faint)" }}>{label}</div>
      <div className="text-2xl tnum leading-tight"
           style={{ color: tone === "accent" ? "var(--accent)" : "var(--text)" }}>
        {value === undefined ? "—" : value.toLocaleString()}
      </div>
      {hint && <div className="text-[11px]"
                    style={{ color: "var(--text-faint)" }}>{hint}</div>}
    </button>
  );
}

/**
 * One job, and the two things you can do with it: open it, or record that you
 * applied. Recording is a real write to the spreadsheet, so it asks first —
 * the confirm step is one click, not a dialog, because this happens dozens of
 * times and a modal each time would be its own kind of punishment.
 */
function JobRow({ job }: { job: Job }) {
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const mark = useMutation({
    mutationFn: () => api.logApplication({
      job_id: job.id, applied_via: "company site",
      action_id: crypto.randomUUID() }),
    onSuccess: () => { setConfirming(false); qc.invalidateQueries(); },
    onError: (e: ApiError) => { setError(e.message); setConfirming(false); },
  });

  return (
    <div className="px-4 py-3" style={{ borderBottom: "1px solid var(--border)" }}>
      <div className="flex items-baseline gap-3">
        <span className="tnum text-[11px] w-8 shrink-0"
              style={{ color: "var(--text-faint)" }}>{job.score}</span>
        <span className="text-[13px] font-medium shrink-0">{job.company}</span>
        <span className="text-[12.5px] flex-1 truncate"
              style={{ color: "var(--text-muted)" }}>{job.title}</span>

        <span className="flex items-center gap-2 shrink-0">
          {job.sponsor_rating && <Pill tone="ok">{job.sponsor_rating}</Pill>}
          {job.location && <Pill>{job.location}</Pill>}
          {job.redirect_url && (
            <a href={job.redirect_url} target="_blank" rel="noreferrer"
               className="px-3 py-1.5 rounded-lg text-[13px] font-medium"
               style={{ background: "var(--bg-raised)",
                        border: "1px solid var(--border)",
                        color: "var(--text)" }}>Open ↗</a>
          )}
          {confirming ? (
            <span className="flex gap-1.5">
              <Button variant="primary" disabled={mark.isPending}
                      onClick={() => mark.mutate()}>
                {mark.isPending ? "Saving…" : "Yes, applied"}
              </Button>
              <Button onClick={() => setConfirming(false)}>Cancel</Button>
            </span>
          ) : (
            <Button onClick={() => { setError(null); setConfirming(true); }}>
              I applied
            </Button>
          )}
        </span>
      </div>
      {error && (
        <div className="mt-2 px-3 py-2 rounded-lg text-[12px]"
             style={{ background: "var(--stop-soft)", color: "var(--stop)" }}>
          {error}
        </div>
      )}
    </div>
  );
}
