/**
 * Every job, and everything known about the one you clicked.
 *
 * Two panes rather than a table with a modal. The list is the thing you scan
 * and the detail is the thing you read, and a modal over a list forces you to
 * close one to see the other — which is the wrong shape for "work down a list
 * deciding about each".
 *
 * The filters are the ones that change what you would do next. There is no
 * salary slider and no date range, because 95% of these have a salary and none
 * has a deadline, so both would be controls that mostly do nothing.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { ApiError, api } from "./api";
import { Button, Empty, Panel, Pill, ServerDown } from "./components";

type Filter = { q: string; status: string; sponsor: string; eligible: string };

const EMPTY: Filter = { q: "", status: "", sponsor: "", eligible: "true" };

export function Jobs() {
  const [filter, setFilter] = useState<Filter>(EMPTY);
  const [openId, setOpenId] = useState<string | null>(null);

  const query = new URLSearchParams({ limit: "150", sort: "score" });
  if (filter.q) query.set("q", filter.q);
  if (filter.status) query.set("status", filter.status);
  if (filter.sponsor) query.set("sponsor", filter.sponsor);
  if (filter.eligible) query.set("eligible", filter.eligible);

  const jobs = useQuery({
    queryKey: ["jobs", "browse", query.toString()],
    queryFn: () => api.jobs(query.toString()),
  });

  if (jobs.isError) {
    return <div className="p-4 max-w-xl mx-auto mt-12">
      <Panel title="Not connected"><ServerDown /></Panel>
    </div>;
  }

  const items = jobs.data?.items ?? [];

  // Pin the selection to an explicit id as soon as there is one.
  //
  // Falling back to items[0] on every render looks harmless and is not: the
  // list refetches after any status change, and if the order shifts, the detail
  // pane silently swaps to a different job while you are looking at it. The
  // next button you press then applies to a job you never chose. That is almost
  // certainly how an "ignored" landed on a role nobody meant to ignore.
  useEffect(() => {
    if (!openId && items.length) setOpenId(items[0].id);
  }, [openId, items]);

  // Never fall back to items[0] once something is selected. If the current job
  // drops out of the filtered list, show nothing rather than quietly retarget.
  const current = openId ? items.find((j) => j.id === openId) ?? null : null;

  return (
    <div className="flex-1 grid gap-4 p-4"
         style={{ gridTemplateColumns: "minmax(340px,1fr) minmax(420px,1.3fr)" }}>

      <Panel title="Jobs" right={<Pill>{jobs.data?.total ?? 0}</Pill>}>
        <div className="px-3 py-2.5 flex flex-col gap-2"
             style={{ borderBottom: "1px solid var(--border)" }}>
          <input
            value={filter.q}
            onChange={(e) => setFilter({ ...filter, q: e.target.value })}
            placeholder="Search title or company…"
            className="w-full px-3 py-1.5 rounded-lg text-[13px]"
            style={{ background: "var(--bg-sunken)", color: "var(--text)",
                     border: "1px solid var(--border)" }} />
          <div className="flex flex-wrap gap-1.5">
            <Choice label="Can apply to" active={filter.eligible === "true"}
                    onClick={() => setFilter({ ...filter,
                      eligible: filter.eligible === "true" ? "" : "true" })} />
            <Choice label="Can sponsor" active={filter.sponsor === "yes"}
                    onClick={() => setFilter({ ...filter,
                      sponsor: filter.sponsor === "yes" ? "" : "yes" })} />
            {["new", "applied", "screening", "interview"].map((s) => (
              <Choice key={s} label={s} active={filter.status === s}
                      onClick={() => setFilter({ ...filter,
                        status: filter.status === s ? "" : s })} />
            ))}
            {(filter.q || filter.status || filter.sponsor || !filter.eligible) && (
              <button onClick={() => setFilter(EMPTY)}
                      className="text-[11px] px-2 underline underline-offset-2"
                      style={{ color: "var(--text-faint)" }}>reset</button>
            )}
          </div>
        </div>

        {jobs.isLoading && <Empty>Loading…</Empty>}
        {items.length === 0 && !jobs.isLoading && (
          <Empty>Nothing matches those filters.</Empty>
        )}
        <div className="max-h-[calc(100vh-230px)] overflow-y-auto">
          {items.map((job) => (
            <button key={job.id} onClick={() => setOpenId(job.id)}
                    className="w-full text-left px-4 py-2.5"
                    style={{
                      background: job.id === current?.id
                        ? "var(--accent-soft)" : "transparent",
                      borderBottom: "1px solid var(--border)",
                      borderLeft: `2px solid ${job.id === current?.id
                        ? "var(--accent)" : "transparent"}`,
                    }}>
              <div className="flex items-baseline gap-2">
                <span className="tnum text-[11px] w-7 shrink-0"
                      style={{ color: "var(--text-faint)" }}>{job.score}</span>
                <span className="text-[13px] font-medium truncate">
                  {job.company}
                </span>
                <span className="flex gap-1 ml-auto shrink-0">
                  {job.status !== "new" && <Pill tone="accent">{job.status}</Pill>}
                  {job.sponsor_rating && <Pill tone="ok">{job.sponsor_rating}</Pill>}
                </span>
              </div>
              <div className="text-[11.5px] truncate mt-0.5"
                   style={{ color: "var(--text-faint)" }}>{job.title}</div>
            </button>
          ))}
        </div>
      </Panel>

      <JobDetail id={current?.id ?? openId} />
    </div>
  );
}

function Choice({ label, active, onClick }: {
  label: string; active: boolean; onClick: () => void;
}) {
  return (
    <button onClick={onClick}
            className="px-2 py-0.5 rounded text-[11px] font-medium capitalize"
            style={active
              ? { background: "var(--accent-soft)", color: "var(--accent)",
                  border: "1px solid var(--accent)" }
              : { background: "var(--bg-sunken)", color: "var(--text-muted)",
                  border: "1px solid var(--border)" }}>{label}</button>
  );
}

/** Everything known about one job, and the two things you can do about it. */
function JobDetail({ id }: { id: string | null }) {
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);

  const job = useQuery({
    queryKey: ["job", id],
    queryFn: () => api.job(id!),
    enabled: !!id,
  });

  const move = useMutation({
    mutationFn: (status: string) => api.setStatus({
      job_id: id!, status, action_id: crypto.randomUUID() }),
    onSuccess: () => { setError(null); qc.invalidateQueries(); },
    onError: (e: ApiError) => setError(e.message),
  });

  if (!id) return <Panel title="Job"><Empty>Pick a job on the left.</Empty></Panel>;
  if (job.isLoading) return <Panel title="Job"><Empty>Loading…</Empty></Panel>;
  const j = job.data;
  if (!j) return <Panel title="Job"><Empty>Not found.</Empty></Panel>;

  return (
    <Panel title={<span>{j.company}</span>}
           right={<span className="flex gap-1">
             <Pill tone={j.eligible ? "ok" : "stop"}>
               {j.eligible ? "can apply" : "filtered out"}
             </Pill>
             <Pill>{j.source}</Pill>
           </span>}>
      <div className="p-4 flex flex-col gap-4 max-h-[calc(100vh-200px)] overflow-y-auto">

        <div>
          <h3 className="text-[15px] font-semibold leading-snug">{j.title}</h3>
          <div className="flex flex-wrap gap-1.5 mt-2">
            {j.location && <Pill>{j.location}</Pill>}
            {j.salary_max && <Pill>up to £{Number(j.salary_max).toLocaleString()}</Pill>}
            {j.age_days !== null && <Pill>posted {j.age_days}d ago</Pill>}
            <Pill tone="accent">score {j.score}</Pill>
          </div>
        </div>

        {/* Why it can or cannot be applied to — the whole point of the pipeline */}
        {!j.eligible && j.disqualified_reason && (
          <Note tone="stop" title="Filtered out">{j.disqualified_reason}</Note>
        )}
        {j.clearance_hint && (
          <Note tone="warn" title="Mentions clearance">
            “{j.clearance_hint}” — flagged, not blocked. Read the posting; this
            may describe other work at the company rather than this role.
          </Note>
        )}

        <Sponsorship resolution={j.resolution} match={j.sponsor_match} />

        {j.score_reasons.length > 0 && (
          <Field label="Why it ranked here">
            <div className="flex flex-wrap gap-1">
              {j.score_reasons.map((r) => <Pill key={r}>{r}</Pill>)}
            </div>
          </Field>
        )}

        <Field label="Status">
          {/* Two groups, because they are two different things and having them
              in one undifferentiated row is how "ignored" gets clicked when
              "interview" was meant. The first row is progress; the second is
              the ways it ends. */}
          <div className="flex flex-wrap gap-1.5 items-center">
            {(["applied", "screening", "interview", "offer"] as const).map((s) => (
              <Button key={s} variant={j.status === s ? "primary" : "ghost"}
                      disabled={move.isPending || j.status === s}
                      onClick={() => move.mutate(s)}>{s}</Button>
            ))}
          </div>
          <div className="flex flex-wrap gap-1.5 items-center mt-1.5">
            {(["rejected", "ignored", "closed"] as const).map((s) => (
              <Button key={s} variant={j.status === s ? "danger" : "ghost"}
                      disabled={move.isPending || j.status === s}
                      onClick={() => move.mutate(s)}>{s}</Button>
            ))}
            {/* The way back. Without this a mis-click is permanent from inside
                the app, which is exactly what happened the first time someone
                used it. */}
            {j.status !== "new" && j.status !== "" && (
              <Button disabled={move.isPending}
                      onClick={() => move.mutate("new")}>↩ undo — back to new</Button>
            )}
          </div>
          {j.date_applied && (
            <div className="text-[11.5px] mt-1.5" style={{ color: "var(--text-faint)" }}>
              applied {j.date_applied}{j.applied_via && ` · ${j.applied_via}`}
            </div>
          )}
          {error && <div className="text-[12px] mt-1.5"
                         style={{ color: "var(--stop)" }}>{error}</div>}
        </Field>

        {j.redirect_url && (
          <a href={j.redirect_url} target="_blank" rel="noreferrer"
             className="px-3 py-2 rounded-lg text-[13px] font-medium text-center"
             style={{ background: "var(--accent)", color: "var(--accent-text)" }}>
            Open the posting ↗
          </a>
        )}

        {j.notes && <Field label="Your notes">
          <p className="text-[12.5px] whitespace-pre-wrap"
             style={{ color: "var(--text-muted)" }}>{j.notes}</p>
        </Field>}

        {j.description && (
          <Field label="The posting">
            <p className="text-[12.5px] leading-relaxed whitespace-pre-wrap"
               style={{ color: "var(--text-muted)" }}>{j.description}</p>
          </Field>
        )}

        {j.decision_trail.length > 0 && (
          <Field label="History">
            {j.decision_trail.map((e) => (
              <div key={e.id} className="text-[11.5px] flex gap-2 py-0.5">
                <span className="mono shrink-0" style={{ color: "var(--text-faint)" }}>
                  {e.ts.slice(0, 10)}
                </span>
                <span style={{ color: "var(--text-muted)" }}>{e.rationale}</span>
              </div>
            ))}
          </Field>
        )}
      </div>
    </Panel>
  );
}

/**
 * Where the sponsorship verdict came from, next to the verdict itself.
 *
 * The tone follows the RESOLUTION, not the row's yes/no flag. An earlier
 * version keyed the heading off `sponsor_match` and the body off the
 * resolution, which produced a green "On the sponsor register" sitting directly
 * above "No entry found under this name" — a self-contradiction on the one
 * badge the whole project exists to make trustworthy.
 *
 * Four states, each naming its own evidence:
 *   alias  a human confirmed it, with their reason
 *   board  the curated board registry maps it
 *   exact  the name is verbatim on the register
 *   none   nothing backs it
 */
function Sponsorship({ resolution, match }: {
  resolution: { method: string; register_name: string; rating: string;
                confirmed_by: string; confirmed_at: string;
                rationale: string } | null;
  match: string;
}) {
  const method = resolution?.method ?? "none";
  const resolved = method !== "none" && !!resolution?.register_name;

  if (!resolved) {
    // The row claiming yes while nothing explains it is worth saying out loud
    // rather than quietly rendering a green badge.
    const claims = match?.toLowerCase() === "yes";
    return (
      <Note tone={claims ? "warn" : "stop"}
            title={claims ? "Marked a sponsor, but unverified"
                          : "Not on the sponsor register"}>
        {claims
          ? "The row says this employer can sponsor, but nothing in the register, the board registry or your confirmed aliases currently backs that. Worth checking before you rely on it."
          : "No entry found under this name. It may still be a sponsor under a different legal name — check the names queue."}
      </Note>
    );
  }

  const source =
    method === "alias" ? "you confirmed this"
    : method === "board" ? "from the board registry"
    : "exact name match";

  return (
    <Note tone="ok" title="On the sponsor register">
      <span className="mono">{resolution!.register_name}</span>
      {resolution!.rating && ` · ${resolution!.rating} rating`}
      <div className="mt-1" style={{ opacity: 0.85 }}>
        {source}
        {method === "alias" && resolution!.confirmed_by &&
          ` — ${resolution!.confirmed_by}${resolution!.confirmed_at
            ? ` on ${resolution!.confirmed_at}` : ""}`}
        {resolution!.rationale && method === "alias" &&
          ` — “${resolution!.rationale}”`}
        {method === "board" &&
          " — mapped by hand when this employer's job board was added"}
        {method === "exact" && " — no human decision needed"}
      </div>
    </Note>
  );
}

const Field = ({ label, children }: {
  label: string; children: React.ReactNode;
}) => (
  <div className="flex flex-col gap-1.5">
    <span className="text-[11px] uppercase tracking-wider"
          style={{ color: "var(--text-faint)" }}>{label}</span>
    {children}
  </div>
);

const Note = ({ tone, title, children }: {
  tone: "ok" | "warn" | "stop"; title: string; children: React.ReactNode;
}) => (
  <div className="px-3 py-2 rounded-lg text-[12.5px]"
       style={{ background: `var(--${tone}-soft)`,
                border: `1px solid var(--${tone})` }}>
    <strong style={{ color: `var(--${tone})` }}>{title}</strong>
    <div className="mt-0.5" style={{ color: "var(--text)" }}>{children}</div>
  </div>
);
