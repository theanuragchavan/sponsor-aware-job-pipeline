/* Queue — what is prepared for Cowork, and what is stopping each one.
 *
 * The gate's verdict is the whole screen. Ten conditions decide whether an
 * agent may finish an application or must hand it back, and the useful question
 * is never "is it green" but "which one is red and can I clear it".
 *
 * So every condition is shown for every job, passing ones included. A screen
 * that only listed failures would make a blocked job and a nearly-ready job
 * look identical, and the difference between one failing condition and six is
 * the difference between a five-minute fix and an abandoned lead. That is the
 * same reason the gate itself returns PASS lines and not just FAIL lines: an
 * approved application has to be as auditable as a blocked one.
 *
 * Uses the design-system primitives in index.css rather than inline styles.
 * They were written and never adopted — .panel, .chip, .row, .sec-hd, .eyebrow
 * all existed and every component reached past them for style={{}}.
 */
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import type { QueueItem } from "./api";
import { Empty, Panel, ServerDown } from "./components";

/* Plain English for each rule. The gate's own identifiers are snake_case and
 * precise; a person scanning a list needs the consequence, not the key. */
const RULE_LABEL: Record<string, string> = {
  ats_recorded: "Recorded session for this form",
  sponsor_confirmed: "Sponsor-confirmed employer",
  posting_live: "Posting still live",
  not_already_applied: "Not already applied",
  not_blacklisted: "Not ruled out",
  cv_attested: "CV verified by content",
  letter_grounded: "Letter grounded in CONTENT_MASTER",
  answers_on_file: "Every question has a written answer",
  no_free_text: "No open-ended questions",
  under_daily_cap: "Under the daily cap",
};

function Verdict({ item }: { item: QueueItem }) {
  const failed = item.conditions.filter((c) => c.result === "fail");
  const passed = item.conditions.length - failed.length;

  return (
    <div className="row row--loose">
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="flex items-baseline gap-2">
          <strong className="truncate">{item.company}</strong>
          <span className="truncate text-muted">{item.title}</span>
        </div>

        <div className="flex flex-wrap items-center gap-2" style={{ marginTop: 6 }}>
          {item.done ? (
            <span className="chip">done</span>
          ) : item.autosubmit ? (
            <span className="chip chip--ok">Cowork may submit</span>
          ) : (
            <span className="chip chip--warn">fill, then stop for you</span>
          )}
          <span className="chip chip--num tnum">
            {passed}/{item.conditions.length}
          </span>
          {item.ats && <span className="chip">{item.ats}</span>}
          {item.sponsor_rating && (
            <span className="chip chip--ok">sponsor {item.sponsor_rating}</span>
          )}
          {!item.has_cv && <span className="chip chip--stop">no CV attached</span>}
          {!item.has_letter && <span className="chip chip--stop">no letter</span>}
        </div>

        {failed.length > 0 && (
          <ul className="prov" style={{ marginTop: 8 }}>
            {failed.map((c) => (
              <li key={c.rule}>
                <span className="eyebrow">{RULE_LABEL[c.rule] ?? c.rule}</span>
                {c.detail && <span className="text-muted"> — {c.detail}</span>}
              </li>
            ))}
          </ul>
        )}
      </div>

      {item.url && (
        <a className="btn btn--sm" href={item.url} target="_blank" rel="noreferrer">
          posting
        </a>
      )}
    </div>
  );
}

export function Queue() {
  const q = useQuery({ queryKey: ["queue"], queryFn: api.queue });

  if (q.isError) return <ServerDown />;
  if (!q.data) return <Empty>Loading…</Empty>;
  const items = q.data;

  const waiting = items.filter((i) => !i.done);
  const ready = waiting.filter((i) => i.autosubmit);

  return (
    <div className="flex flex-col gap-4 p-4 max-w-5xl mx-auto w-full">
      <Panel
        title="Prepared applications"
        note={
          waiting.length === 0
            ? undefined
            : `${ready.length} of ${waiting.length} clear all ten conditions`
        }
      >
        {waiting.length === 0 ? (
          <Empty>
            Nothing prepared yet. Build one with{" "}
            <code className="mono">python package\build_package.py &lt;job id&gt;</code>.
          </Empty>
        ) : (
          <div className="copies">
            {waiting.map((i) => (
              <Verdict key={i.job_id} item={i} />
            ))}
          </div>
        )}
      </Panel>

      {items.some((i) => i.done) && (
        <Panel title="Already run" note="kept as a record of what was prepared">
          <div className="copies">
            {items
              .filter((i) => i.done)
              .map((i) => (
                <Verdict key={i.job_id} item={i} />
              ))}
          </div>
        </Panel>
      )}
    </div>
  );
}
