/**
 * Who might get you in the door at this company.
 *
 * Three groups, deliberately not merged into one ranked list, because the
 * confidence behind each is different:
 *
 *   Saved      people you already found and what happened with each
 *   GitHub     real, named, verifiable — but only those who made org
 *              membership public, so a company of thousands may show thirty
 *   LinkedIn   searches YOU run, in your own browser. Nothing is fetched from
 *              LinkedIn here, ever
 *
 * Ranking them together would imply an equivalence that is not there: a GitHub
 * org member is a confirmed employee, a repo contributor may be a stranger who
 * liked the project, and a LinkedIn search is a question rather than an answer.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "./api";
import type { ContactRoute, Person, SavedContact } from "./api";
import { Button, Pill } from "./components";

export function Referrals({ company, jobId, jobTitle = "" }: {
  company: string; jobId: string; jobTitle?: string;
}) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);

  const data = useQuery({
    queryKey: ["referrals", company],
    queryFn: () => api.referrals(company),
    enabled: open,               // 30+ GitHub calls; only on request
    staleTime: 6 * 60 * 60 * 1000,
  });

  const save = useMutation({
    mutationFn: (p: Person) => api.saveContact({
      company, name: p.name, source: "github", handle: p.login,
      url: p.url, location: p.location, routes: p.contact_routes,
      signals: p.signals.map((s) => s.key), job_id: jobId }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["referrals", company] }),
  });

  const move = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) =>
      api.setContactStatus(id, status),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["referrals", company] }),
  });

  if (!open) {
    return (
      <div className="flex flex-col gap-1.5">
        <Label>Referrals</Label>
        <Button onClick={() => setOpen(true)}>
          Find someone at {company}
        </Button>
        <span className="text-[11px]" style={{ color: "var(--text-faint)" }}>
          A referral is worth more than a better cover letter. This looks up real
          people and builds the LinkedIn searches worth running.
        </span>
      </div>
    );
  }

  const d = data.data;

  return (
    <div className="flex flex-col gap-3">
      <Label>Referrals at {company}</Label>

      {data.isLoading && (
        <span className="text-[12.5px]" style={{ color: "var(--text-faint)" }}>
          Looking up GitHub… this takes a few seconds the first time.
        </span>
      )}

      {/* 1 — people already saved, and where each one got to */}
      {!!d?.saved.length && (
        <Group title="You've found">
          {d.saved.map((c) => <Saved key={c.id} c={c} onMove={move.mutate} />)}
        </Group>
      )}

      {/* 2 — real, named people */}
      {d && (
        <Group title={d.github.org
          ? `On GitHub (${d.github.people.length})`
          : "On GitHub"}>
          {!d.github.org && (
            <Note>No public GitHub organisation for {company}. Common for
              non-technical employers, and for companies that keep their org
              private. The LinkedIn searches below still work.</Note>
          )}
          {d.github.error && <Note>{d.github.error}</Note>}
          {d.github.people.slice(0, 8).map((p) => {
            const already = d.saved.some((s) => s.handle === p.login);
            return (
              <div key={p.login} className="py-1.5"
                   style={{ borderBottom: "1px solid var(--border)" }}>
                <div className="flex items-baseline gap-2">
                  <a href={p.url} target="_blank" rel="noreferrer"
                     className="text-[12.5px] font-medium underline
                                underline-offset-2 whitespace-nowrap">
                    {p.name}
                  </a>
                  {p.location && (
                    <span className="text-[11px]"
                          style={{ color: "var(--text-faint)" }}>{p.location}</span>
                  )}
                  <span className="flex gap-1 ml-auto shrink-0 items-center">
                    {p.signals.map((s) => (
                      <Pill key={s.key} tone="accent">{s.label}</Pill>
                    ))}
                    <Pill>{p.relationship}</Pill>
                    {already
                      ? <Pill tone="ok">saved</Pill>
                      : <Button disabled={save.isPending}
                                onClick={() => save.mutate(p)}>save</Button>}
                  </span>
                </div>
                <Routes routes={p.contact_routes} />
                <DraftMessage
                  person={p.name} company={company} jobId={jobId}
                  jobTitle={jobTitle}
                  signals={p.signals.map((s) => s.key)}
                  evidence={Object.fromEntries(
                    p.signals.map((s) => [s.key, s.evidence ?? ""]))}
                  channel={p.contact_routes[0]?.value ?? "LinkedIn"} />
              </div>
            );
          })}
          {!!d.github.people.length && (
            <div className="flex flex-col gap-1 mt-1">
              <Note>The small links under a name are contact routes that person
                published themselves — GitHub hides the email field by default,
                so an address showing here is one they chose to make public.
                Nothing is guessed. Someone with no links published no route,
                and LinkedIn is the way to reach them.</Note>
              <Note>Only people who made their org membership public appear
                here, so this is a starting point rather than a staff list. A
                “contributor” may not work there at all.</Note>
            </div>
          )}
        </Group>
      )}

      {/* 3 — Google's index of the same profiles, without LinkedIn's limits */}
      {d && !!(d as any).xray_searches?.length && (
        <Group title="Search Google (no login, no limits)">
          <Note>LinkedIn caps how many profiles you can view in a month. Google
            indexes the same public profiles with no such cap, and two quoted
            terms is a sharper filter than LinkedIn's keyword box.</Note>
          <div className="flex flex-col gap-1 mt-1.5">
            {(d as any).xray_searches.map((s: any) => (
              <a key={s.key} href={s.url} target="_blank" rel="noreferrer"
                 className="px-3 py-1.5 rounded-lg text-[12.5px]"
                 style={{ background: "var(--bg-sunken)",
                          border: "1px solid var(--border)" }}>
                <span className="font-medium">{s.label}</span>
                <span className="block text-[11px] mt-0.5"
                      style={{ color: "var(--text-faint)" }}>{s.why}</span>
              </a>
            ))}
          </div>
        </Group>
      )}

      {/* 4 — the searches only he can run */}
      {d && (
        <Group title="Search LinkedIn yourself">
          <Note>These open in your browser, logged in as you. Nothing is read
            from LinkedIn by this app — and your university, languages and
            hometown are things people state there but not on GitHub, so this is
            where they pay off.</Note>
          <div className="flex flex-col gap-1 mt-1.5">
            {d.linkedin_searches.map((s) => (
              <a key={s.key} href={s.url} target="_blank" rel="noreferrer"
                 className="px-3 py-1.5 rounded-lg text-[12.5px]"
                 style={{ background: "var(--bg-sunken)",
                          border: "1px solid var(--border)" }}>
                <span className="font-medium">{s.label}</span>
                <span className="block text-[11px] mt-0.5"
                      style={{ color: "var(--text-faint)" }}>{s.why}</span>
              </a>
            ))}
          </div>
        </Group>
      )}
    </div>
  );
}

/**
 * How to reach one person, using only what they published themselves.
 *
 * Every route here came off their own profile or their own site. Nothing is
 * guessed from a name-and-domain pattern, which is the thing that produces
 * bounces, spam folders and "how did you get this address?" — and which is
 * also what every paid contact-finder is quietly doing underneath.
 *
 * Deliberately not a one-click "message" button. The route is shown; the
 * decision to use it, and what to say, stays with him.
 */
const ROUTE_ICON: Record<string, string> = {
  website: "site", email: "email", x: "X",
};

function Routes({ routes }: { routes?: ContactRoute[] }) {
  if (!routes?.length) return null;
  return (
    <div className="flex flex-wrap gap-1 mt-1 pl-0.5">
      {routes.map((r) => (
        <a key={r.kind} href={r.url} target="_blank" rel="noreferrer"
           title={r.note}
           className="px-2 py-0.5 rounded-md text-[11px] flex items-baseline gap-1.5
                      max-w-[260px]"
           style={{ background: "var(--bg-sunken)",
                    border: "1px solid var(--border)" }}>
          <span style={{ color: "var(--text-faint)" }}>
            {ROUTE_ICON[r.kind] ?? r.kind}
          </span>
          <span className="truncate" style={{ color: "var(--accent)" }}>
            {r.value}
          </span>
        </a>
      ))}
    </div>
  );
}

/**
 * The first message, drafted but never sent.
 *
 * Two lengths because there are two channels and they are not interchangeable:
 * a connection note has a hard character limit, an email does not. Both open
 * on the shared signal that actually matched, and neither asks for a referral —
 * that ask belongs after they have replied once, and the server refuses to
 * generate it either way.
 *
 * The hook is shown with the profile text that produced it, because only he can
 * tell a real match from a coincidence, and sending a message built on a wrong
 * one is worse than sending nothing.
 */
function DraftMessage({ person, company, jobId, jobTitle, signals, evidence,
                        channel }: {
  person: string; company: string; jobId: string; jobTitle: string;
  signals: string[]; evidence: Record<string, string>; channel: string;
}) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState("");

  const draft = useMutation({
    mutationFn: (save: boolean) => api.draftOutreach({
      person_name: person, company, job_id: jobId, job_title: jobTitle,
      signals, evidence_by_signal: evidence, channel, save }),
  });

  function copy(which: "note" | "message", text: string) {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(which);
      setTimeout(() => setCopied(""), 1500);
    }).catch(() => undefined);
  }

  if (!open) {
    return (
      <button className="text-[11px] underline underline-offset-2 mt-1"
              style={{ color: "var(--text-faint)" }}
              onClick={() => { setOpen(true); draft.mutate(false); }}>
        draft a message
      </button>
    );
  }

  const d = draft.data;

  return (
    <div className="mt-2 p-3 rounded-lg flex flex-col gap-2"
         style={{ background: "var(--bg-sunken)",
                  border: "1px solid var(--border)" }}>
      {draft.isPending && (
        <Note>Drafting…</Note>
      )}

      {d && (
        <>
          <Note>
            {d.hook_key
              ? <>Opens on <strong>{d.hook_key}</strong>
                  {d.hook_evidence
                    ? <> — their profile says “{d.hook_evidence}”</> : null}.
                  Check that is a real match before you send it.</>
              : <>No shared signal matched, so it opens on their work instead.</>}
          </Note>

          <Block label={`Short version — ${d.note_length}/${d.note_limit} characters, `
                        + `fits a LinkedIn connection note`}
                 text={d.note} copied={copied === "note"}
                 onCopy={() => copy("note", d.note)} />

          <Block label="Longer version — email or their contact form"
                 text={d.message} copied={copied === "message"}
                 onCopy={() => copy("message", d.message)} />

          {!!d.warnings.length && (
            <div className="text-[11px] px-2 py-1.5 rounded"
                 style={{ background: "var(--stop-soft)", color: "var(--stop)" }}>
              {d.warnings.join(" · ")}
            </div>
          )}

          <div className="flex items-center gap-2">
            <Button disabled={draft.isPending}
                    onClick={() => draft.mutate(true)}>
              Save to a file
            </Button>
            <Button onClick={() => setOpen(false)}>Close</Button>
            {d.saved_to && (
              <span className="text-[11px] mono truncate"
                    style={{ color: "var(--ok)" }}>saved</span>
            )}
          </div>

          <Note>Nothing is sent from here. Edit it, then send it yourself —
            and never ask for the referral in the first message. Ask the
            question, and the offer usually comes on its own.</Note>
        </>
      )}
    </div>
  );
}

function Block({ label, text, copied, onCopy }: {
  label: string; text: string; copied: boolean; onCopy: () => void;
}) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-baseline gap-2">
        <span className="text-[10.5px] uppercase tracking-wider"
              style={{ color: "var(--text-faint)" }}>{label}</span>
        <button onClick={onCopy}
                className="text-[11px] underline underline-offset-2 ml-auto shrink-0"
                style={{ color: copied ? "var(--ok)" : "var(--accent)" }}>
          {copied ? "copied" : "copy"}
        </button>
      </div>
      <div className="text-[12px] leading-relaxed whitespace-pre-wrap rounded p-2"
           style={{ background: "var(--bg-raised)",
                    border: "1px solid var(--border)" }}>
        {text}
      </div>
    </div>
  );
}

function Saved({ c, onMove }: {
  c: SavedContact; onMove: (a: { id: string; status: string }) => void;
}) {
  const next: Record<string, string> = {
    found: "contacted", contacted: "replied", replied: "referred",
  };
  const step = next[c.status];
  return (
    <div className="py-1.5" style={{ borderBottom: "1px solid var(--border)" }}>
      <div className="flex items-baseline gap-2">
      <a href={c.url} target="_blank" rel="noreferrer"
         className="text-[12.5px] font-medium underline underline-offset-2">
        {c.name}
      </a>
      <Pill tone={c.status === "referred" ? "ok"
                : c.status === "declined" ? "stop" : "accent"}>{c.status}</Pill>
      {c.contacted_on && (
        <span className="text-[11px]" style={{ color: "var(--text-faint)" }}>
          {c.contacted_on}
        </span>
      )}
      <span className="ml-auto flex gap-1 shrink-0">
        {step && (
          <Button onClick={() => onMove({ id: c.id, status: step })}>
            mark {step}
          </Button>
        )}
        {c.status !== "declined" && (
          <Button variant="danger"
                  onClick={() => onMove({ id: c.id, status: "declined" })}>
            no reply
          </Button>
        )}
      </span>
      </div>
      <Routes routes={c.routes} />
      <DraftMessage
        person={c.name} company={c.company} jobId={c.job_ids[0] ?? ""}
        jobTitle="" signals={c.signals} evidence={{}}
        channel={c.routes?.[0]?.value ?? "LinkedIn"} />
    </div>
  );
}

const Group = ({ title, children }: {
  title: string; children: React.ReactNode;
}) => (
  <div className="flex flex-col gap-0.5">
    <span className="text-[11px] font-medium">{title}</span>
    {children}
  </div>
);

const Note = ({ children }: { children: React.ReactNode }) => (
  <span className="text-[11px] leading-snug"
        style={{ color: "var(--text-faint)" }}>{children}</span>
);

const Label = ({ children }: { children: React.ReactNode }) => (
  <span className="text-[11px] uppercase tracking-wider"
        style={{ color: "var(--text-faint)" }}>{children}</span>
);

export default Referrals;
