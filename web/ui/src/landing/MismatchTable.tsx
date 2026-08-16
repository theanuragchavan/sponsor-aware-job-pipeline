/**
 * The five pairs again, but as a record rather than a diagram.
 *
 * The hero shows the shape of the problem. This shows the mechanism, which is
 * the third column: which rule actually found each pair, and the two that no
 * rule reaches. Repeating the same five names without adding that column would
 * be repeating the hero at a lower resolution.
 *
 * Rows reveal brand-first, entity-second, with a beat between them. That order
 * is the real one — you have the brand, the legal entity is the thing you have
 * to go and find — and it is the only reason the reveal is staggered at all
 * rather than the rows simply appearing together.
 */
import { useRef } from "react";
import { PAIRS, RULE_LABEL } from "./data";
import { useRevealOnView } from "./motion-hooks";

const ROW_STAGGER = 64;   // ms between rows, inside the 30–80ms band
const CELL_OFFSET = 90;   // ms between the brand cell and the entity cell

export function MismatchTable() {
  const ref = useRef<HTMLDivElement>(null);
  useRevealOnView(ref, "rise", 0.2);

  return (
    <div ref={ref}>
      {/* Outside the scroller: at 390px the table is wider than the screen,
          and a <caption> inside it gets clipped along with the third column. */}
      <p className="lp-note" id="mismatch-note">
        Every register name below is the exact string the Home Office publishes,
        reproduced without normalisation.
      </p>
      <div className="lp-scroll">
      <table className="lp-table" aria-describedby="mismatch-note">
        <thead>
          <tr>
            <th scope="col">Job board</th>
            <th scope="col">Home Office register</th>
            <th scope="col">Found by</th>
          </tr>
        </thead>
        <tbody>
          {PAIRS.map((pair, i) => (
            <tr key={pair.brand}>
              <td className="lp-cell-id"
                  data-reveal data-delay={i * ROW_STAGGER}>
                {pair.brand}
              </td>
              <td className="lp-cell-id lp-cell-id-muted"
                  data-reveal data-delay={i * ROW_STAGGER + CELL_OFFSET}>
                {pair.register}
              </td>
              <td className="lp-cell-rule" data-none={pair.rule === null}
                  data-reveal data-delay={i * ROW_STAGGER + CELL_OFFSET}>
                {pair.rule ? RULE_LABEL[pair.rule] : "no rule reaches it"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </div>
  );
}
