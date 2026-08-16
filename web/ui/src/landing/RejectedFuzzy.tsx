/**
 * The documented negative.
 *
 * These three matches are what difflib similarity scoring proposed when it was
 * measured against the real register. They are struck through rather than
 * greyed out, because greying says "less important" and a strike says
 * "recorded, then refused", which is what actually happened to them.
 *
 * There are no similarity scores on screen. The rejection is recorded in the
 * repository as a list of false positives, not as a threshold sweep, so any
 * number here would be invented.
 *
 * The strike is a real element rather than a ::after, so it can be animated
 * from a keyframe rather than parked in a hidden CSS state. The struck state is
 * what the stylesheet holds; the animation only ever undraws it for the 240ms
 * it takes to draw it back.
 */
import { useRef } from "react";
import { REJECTED } from "./data";
import { useRevealOnView } from "./motion-hooks";

export function RejectedFuzzy() {
  const ref = useRef<HTMLDivElement>(null);
  useRevealOnView(ref, "strike", 0.35);

  return (
    <div ref={ref}>
      <p className="lp-note" id="rejected-note">
        <code className="mono">difflib.SequenceMatcher</code>, run against the
        live register. Proposed, then refused.
      </p>
      <div className="lp-scroll">
      <table className="lp-table" aria-describedby="rejected-note">
        <thead>
          <tr>
            <th scope="col">Query</th>
            <th scope="col">What similarity scoring returned</th>
            <th scope="col">Verdict</th>
          </tr>
        </thead>
        <tbody>
          {REJECTED.map((row, i) => (
            <tr key={row.query}>
              <td className="lp-cell-id">{row.query}</td>
              <td className="lp-cell-id lp-cell-id-muted">
                <span className="lp-struck">
                  {row.proposed}
                  <span className="lp-strike" aria-hidden="true"
                        data-reveal data-delay={120 + i * 120} />
                </span>
              </td>
              <td><span className="lp-verdict">not the same employer</span></td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </div>
  );
}
