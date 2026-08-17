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
import type { Person, SavedContact } from "./api";
import { Button, Pill } from "./components";

export function Referrals({ company, jobId }: {
  company: string; jobId: string;
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
      url: p.url, location: p.location,
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
              <div key={p.login} className="flex items-baseline gap-2 py-1.5"
                   style={{ borderBottom: "1px solid var(--border)" }}>
                <a href={p.url} target="_blank" rel="noreferrer"
                   className="text-[12.5px] font-medium underline underline-offset-2">
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
            );
          })}
          {!!d.github.people.length && (
            <Note>Only people who made their org membership public appear here,
              so this is a starting point rather than a staff list. A
              “contributor” may not work there at all.</Note>
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

function Saved({ c, onMove }: {
  c: SavedContact; onMove: (a: { id: string; status: string }) => void;
}) {
  const next: Record<string, string> = {
    found: "contacted", contacted: "replied", replied: "referred",
  };
  const step = next[c.status];
  return (
    <div className="flex items-baseline gap-2 py-1.5"
         style={{ borderBottom: "1px solid var(--border)" }}>
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
