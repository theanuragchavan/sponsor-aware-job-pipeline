/**
 * Press a button, watch the job search run.
 *
 * The 09:00 scheduled task is invisible: it either worked or it did not, and
 * there is no way to tell from the app. Worse, it only pulls from Adzuna — the
 * employer job boards, which is where Palantir, Monzo, OpenAI, Faculty and
 * Snowflake come from, are not on the schedule at all.
 *
 * So: two buttons, live output, and the board run is a dry run. It reports what
 * it *would* add without adding it, because that merge is insert-only and never
 * revisits an id — a mapping bug there has to be undone by hand in Excel.
 */
import { useEffect, useRef, useState } from "react";
import { Button, Panel, Pill } from "./components";

type Status = {
  kind: string | null;
  running: boolean;
  seconds: number;
  returncode: number | null;
  error: string;
  lines: string[];
  total_lines: number;
};

export function RunSearch() {
  const [lines, setLines] = useState<string[]>([]);
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState<string | null>(null);
  const seen = useRef(0);
  const log = useRef<HTMLDivElement>(null);

  // Poll only while something is running. A socket would be more machinery for
  // one user, one browser and a run measured in minutes.
  useEffect(() => {
    if (!status?.running) return;
    const t = setInterval(async () => {
      const res = await fetch(`/api/actions/run-search?since=${seen.current}`);
      if (!res.ok) return;
      const s: Status = await res.json();
      if (s.lines.length) {
        setLines((prev) => [...prev, ...s.lines]);
        seen.current = s.total_lines;
      }
      setStatus(s);
    }, 1500);
    return () => clearInterval(t);
  }, [status?.running]);

  useEffect(() => {
    log.current?.scrollTo({ top: log.current.scrollHeight });
  }, [lines]);

  async function start(kind: "adzuna" | "boards") {
    setError(null);
    setLines([]);
    seen.current = 0;
    const res = await fetch(`/api/actions/run-search?kind=${kind}`,
                            { method: "POST" });
    const body = await res.json();
    if (!res.ok) {
      setError(body?.error?.message ?? "Could not start.");
      return;
    }
    setStatus(body);
  }

  const running = !!status?.running;
  const done = status && !status.running && lines.length > 0;

  return (
    <Panel
      title="Look for new jobs"
      right={running
        ? <Pill tone="accent">running · {status!.seconds}s</Pill>
        : done
          ? <Pill tone={status!.returncode === 0 ? "ok" : "stop"}>
              {status!.returncode === 0 ? "finished" : `exit ${status!.returncode}`}
            </Pill>
          : undefined}>
      <div className="p-4 flex flex-col gap-3">
        <div className="flex flex-wrap gap-2 items-center">
          <Button variant="primary" disabled={running}
                  onClick={() => start("adzuna")}>
            Search the job boards
          </Button>
          <Button disabled={running} onClick={() => start("boards")}>
            Check employer sites (preview only)
          </Button>
        </div>

        <p className="text-[11.5px] leading-snug"
           style={{ color: "var(--text-faint)" }}>
          The first runs the same search your computer does at 9am, now.
          The second checks Palantir, Monzo, OpenAI, Faculty and the other
          employers' own career pages — <strong>which the 9am run does not do</strong>.
          It only shows you what it found; nothing is saved until you say so.
        </p>

        {error && (
          <div className="px-3 py-2 rounded-lg text-[12.5px]"
               style={{ background: "var(--stop-soft)", color: "var(--stop)" }}>
            {error}
          </div>
        )}

        {!!lines.length && (
          <div ref={log}
               className="mono text-[11px] leading-relaxed rounded-lg p-3
                          max-h-72 overflow-y-auto whitespace-pre-wrap"
               style={{ background: "var(--bg-sunken)",
                        border: "1px solid var(--border)",
                        color: "var(--text-muted)" }}>
            {lines.join("\n")}
          </div>
        )}
      </div>
    </Panel>
  );
}

/** People you messaged who have not replied, and what to send next. */
export function DueNudges() {
  const [due, setDue] = useState<Array<{
    id: string; name: string; company: string; url: string;
    contacted_on: string; days: number; touch: number; what_to_send: string;
  }>>([]);

  useEffect(() => {
    fetch("/api/contacts/due")
      .then((r) => (r.ok ? r.json() : []))
      .then(setDue)
      .catch(() => undefined);
  }, []);

  if (!due.length) return null;

  return (
    <Panel title="Waiting on a reply" right={<Pill tone="warn">{due.length}</Pill>}>
      <div className="px-4 py-3 flex flex-col gap-2">
        <p className="text-[11.5px]" style={{ color: "var(--text-faint)" }}>
          Most referral conversations happen on the second or third message, not
          the first. People are busy, not uninterested.
        </p>
        {due.map((d) => (
          <div key={d.id} className="flex items-baseline gap-2 py-1"
               style={{ borderTop: "1px solid var(--border)" }}>
            <a href={d.url} target="_blank" rel="noreferrer"
               className="text-[12.5px] font-medium underline underline-offset-2">
              {d.name}
            </a>
            <span className="text-[11.5px]"
                  style={{ color: "var(--text-muted)" }}>{d.company}</span>
            <Pill tone="warn">day {d.touch}</Pill>
            <span className="text-[11px] ml-auto text-right"
                  style={{ color: "var(--text-faint)" }}>
              {d.days}d ago — {d.what_to_send}
            </span>
          </div>
        ))}
      </div>
    </Panel>
  );
}
