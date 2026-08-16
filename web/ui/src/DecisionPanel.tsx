/**
 * The decision surface for one company.
 *
 * The flow it enforces is the whole point of the application:
 *
 *   pick a candidate -> the system previews the exact consequence
 *   -> you write down why -> you commit -> it is recorded against your name
 *
 * The preview number is not decorative. It comes from the same validators and
 * the same effect computation the apply path runs, so "this moves 12 jobs onto
 * your shortlist" is a promise the next call keeps.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { ApiError, api } from "./api";
import type { ActionResponse, ReviewItem, Suggestion } from "./api";
import { Button, Empty, Panel, Pill, ValidationList } from "./components";

const MIN_RATIONALE = 10;

export function DecisionPanel({ item }: { item: ReviewItem | null }) {
  const qc = useQueryClient();
  const [picked, setPicked] = useState<Suggestion | null>(null);
  const [rationale, setRationale] = useState("");
  const [preview, setPreview] = useState<ActionResponse | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [done, setDone] = useState<ActionResponse | null>(null);
  const [actionId, setActionId] = useState(() => crypto.randomUUID());

  // Reset everything when the selection changes. Carrying a rationale from one
  // company to the next would attach a reason to a decision it was not written
  // for, which is worse than having no reason at all.
  useEffect(() => {
    setPicked(null); setRationale(""); setPreview(null);
    setError(null); setDone(null); setActionId(crypto.randomUUID());
  }, [item?.key]);

  const previewMut = useMutation({
    mutationFn: (s: Suggestion) => api.previewResolve({
      company: item!.company, register_name: s.register_name,
      rationale: "preview only, not a decision", source_rule: s.why }),
    onSuccess: (res) => { setPreview(res); setError(null); },
    onError: (e: ApiError) => { setError(e); setPreview(null); },
  });

  const commitMut = useMutation({
    mutationFn: (opts: { acknowledge_agency?: boolean }) => api.resolve({
      company: item!.company, register_name: picked!.register_name,
      rationale, source_rule: picked!.why, action_id: actionId,
      ...opts }),
    onSuccess: (res) => {
      setDone(res); setError(null);
      // One decision changes the queue, the counts, and the log. Refetch all
      // of it rather than patching caches by hand: an action touching a dozen
      // rows across several views is exactly where optimistic UI starts lying.
      qc.invalidateQueries();
    },
    onError: (e: ApiError) => setError(e),
  });

  if (!item) {
    return (
      <Panel title="Decision">
        <Empty>Pick a company on the left.<br />
          <span className="text-[12px]">
            Each one is a name the matcher could not resolve on its own.
          </span>
        </Empty>
      </Panel>
    );
  }

  if (done) {
    const n = done.effects.rows_changed ?? 0;
    const delta = done.effects.shortlist_delta ?? 0;
    return (
      <Panel title="Recorded">
        <div className="p-4 flex flex-col gap-3">
          <div className="text-[13px]">
            <strong>{item.company}</strong> is now{" "}
            <span className="mono">{done.effects.register_name}</span>.
          </div>
          <div className="flex gap-4 text-[13px]">
            <span><strong className="tnum">{n}</strong> row{n === 1 ? "" : "s"} restamped</span>
            <span><strong className="tnum">{delta}</strong> now shortlist-eligible</span>
          </div>
          <div className="text-[11px] mono" style={{ color: "var(--text-faint)" }}>
            log entry {done.log_entry_id}
          </div>
        </div>
      </Panel>
    );
  }

  const tooShort = rationale.trim().length < MIN_RATIONALE;
  const agencyBlocked = error?.code === "agency_company";

  return (
    <Panel title={<span>Decision · {item.company}</span>}
           right={<Pill tone={item.priority ? "accent" : "muted"}>
             {item.rows} tracked
           </Pill>}>
      <div className="p-4 flex flex-col gap-4">

        {/* 1. the candidates the two deterministic rules proposed */}
        <div className="flex flex-col gap-1.5">
          <Label>Register candidates</Label>
          {item.suggestions.map((s) => {
            const active = picked?.register_name === s.register_name;
            return (
              <button key={s.register_name}
                onClick={() => { setPicked(s); setPreview(null);
                                 setError(null); previewMut.mutate(s); }}
                className="text-left px-3 py-2 rounded-lg transition-colors"
                style={{
                  background: active ? "var(--accent-soft)" : "var(--bg-sunken)",
                  border: `1px solid ${active ? "var(--accent)" : "var(--border)"}`,
                }}>
                <div className="flex items-center justify-between gap-2">
                  <span className="mono text-[12.5px]">{s.register_name}</span>
                  <span className="flex gap-1 shrink-0">
                    {s.rating && <Pill tone={s.rating === "A" ? "ok" : "warn"}>
                      {s.rating}
                    </Pill>}
                    <Pill>{s.why}</Pill>
                  </span>
                </div>
              </button>
            );
          })}
          <p className="text-[11px] leading-snug" style={{ color: "var(--text-faint)" }}>
            The suggester favours precision you can check over cleverness, so it
            offers obvious rubbish next to the real answer and lets you pick.
            Leaving this company undecided is a valid outcome.
          </p>
        </div>

        {/* 2. what the system checked, and what it would do */}
        {previewMut.isPending && <Muted>Checking…</Muted>}

        {preview && (
          <div className="flex flex-col gap-3 pt-3"
               style={{ borderTop: "1px solid var(--border)" }}>
            <Label>Checks</Label>
            {/* The preview sends a placeholder rationale so the other four
                validators can run, so its `rationale_present` pass is about
                the placeholder, not about anything the user wrote. Showing a
                tick for it would be a small lie in the one component whose
                entire job is to be trustworthy. The field below is the live
                indicator for that rule. */}
            <ValidationList validations={preview.validations.filter(
              (v) => v.rule !== "rationale_present")} />
            <Consequence res={preview} />
          </div>
        )}

        {error && (
          <div className="flex flex-col gap-2 p-3 rounded-lg"
               style={{ background: "var(--stop-soft)",
                        border: "1px solid var(--stop)" }}>
            <div className="text-[12.5px]" style={{ color: "var(--stop)" }}>
              {error.message}
            </div>
            {error.validations.length > 0 &&
              <ValidationList validations={error.validations} />}
          </div>
        )}

        {/* 3. the reason, which is the part worth anything in six months */}
        {picked && preview && (
          <div className="flex flex-col gap-1.5 pt-3"
               style={{ borderTop: "1px solid var(--border)" }}>
            <Label>Why is this the same employer?</Label>
            <textarea
              value={rationale}
              onChange={(e) => setRationale(e.target.value)}
              rows={2} maxLength={500}
              placeholder="e.g. Companies House lists this as the trading name; the careers page confirms it."
              className="w-full px-3 py-2 rounded-lg text-[13px] resize-y"
              style={{ background: "var(--bg-sunken)", color: "var(--text)",
                       border: "1px solid var(--border)" }} />
            <div className="flex items-center justify-between">
              <span className="text-[11px]" style={{ color: "var(--text-faint)" }}>
                {tooShort
                  ? `${MIN_RATIONALE - rationale.trim().length} more characters`
                  : "Recorded against your name, permanently."}
              </span>
              <div className="flex gap-2">
                {agencyBlocked && (
                  <Button variant="danger" disabled={tooShort}
                          onClick={() => commitMut.mutate({ acknowledge_agency: true })}>
                    Confirm anyway
                  </Button>
                )}
                <Button variant="primary"
                        disabled={tooShort || commitMut.isPending}
                        onClick={() => commitMut.mutate({})}>
                  {commitMut.isPending ? "Writing…" : "Confirm & write back"}
                </Button>
              </div>
            </div>
          </div>
        )}
      </div>
    </Panel>
  );
}

function Consequence({ res }: { res: ActionResponse }) {
  const n = res.effects.rows_changed ?? 0;
  const delta = res.effects.shortlist_delta ?? 0;
  if (!n) return <Muted>Already resolved — nothing would change.</Muted>;
  return (
    <div className="px-3 py-2.5 rounded-lg text-[13px]"
         style={{ background: "var(--accent-soft)",
                  border: "1px solid var(--accent)" }}>
      Confirming this restamps <strong className="tnum">{n}</strong> row
      {n === 1 ? "" : "s"} and moves{" "}
      <strong className="tnum">{delta}</strong> onto your shortlist.
      <div className="mt-1.5 flex flex-col gap-0.5 max-h-24 overflow-y-auto">
        {res.changes.slice(0, 8).map((c) => (
          <div key={c.entity} className="text-[11px] flex gap-2">
            <span className="mono shrink-0" style={{ color: "var(--text-faint)" }}>
              {c.entity.replace("job:", "")}
            </span>
            <span className="truncate" style={{ color: "var(--text-muted)" }}>
              {c.title}
            </span>
          </div>
        ))}
        {res.changes.length > 8 && (
          <div className="text-[11px]" style={{ color: "var(--text-faint)" }}>
            +{res.changes.length - 8} more
          </div>
        )}
      </div>
    </div>
  );
}

const Label = ({ children }: { children: React.ReactNode }) => (
  <span className="text-[11px] uppercase tracking-wider"
        style={{ color: "var(--text-faint)" }}>{children}</span>
);

const Muted = ({ children }: { children: React.ReactNode }) => (
  <span className="text-[12.5px]" style={{ color: "var(--text-faint)" }}>
    {children}
  </span>
);
