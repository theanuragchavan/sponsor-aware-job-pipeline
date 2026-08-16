/**
 * The hero: an entity graph that assembles itself.
 *
 * This animation is the argument, so it is built to be true rather than
 * impressive. Three things in it carry meaning and none of them are decoration:
 *
 *   1. The left column is in posting order and the right column is in the
 *      register's alphabetical order, so the edges genuinely cross. The
 *      crossing IS the problem — the two sides agree on an ordering no more
 *      than they agree on a name.
 *
 *   2. Four short brands on the left against long, ragged legal entities on the
 *      right. That shape is the data's own and it makes the point before
 *      anybody reads a word.
 *
 *   3. Monzo → MONZO BANK and Deliveroo → ROOFOODS LTD T A DELIVEROO land last
 *      and land accented, because those are the two pairs a reader is most
 *      likely to have an intuition about, and the intuition is wrong.
 *
 * The settled state is what React renders. Motion hides the nodes inside a
 * layout effect and animates them back, so the browser never paints the
 * pre-state and a page whose script fails is already correct. The animation can
 * only ever be subtracted from a working page, never added to a broken one.
 */
import { useCallback, useLayoutEffect, useRef, useState } from "react";
import { animate } from "motion";
import type { AnimationPlaybackControls } from "motion";
import { FACTS, PAIRS, SNAP } from "./data";
import { EASE_OUT, usePrefersReducedMotion } from "./motion-hooks";

/** The register is alphabetical. This is why the lines cross. */
const REGISTER_ORDER = [...PAIRS]
  .sort((a, b) => a.register.localeCompare(b.register))
  .map((p) => p.brand);

/** Endpoints in the field's own coordinate space. */
type Edge = { x1: number; y1: number; x2: number; y2: number };

/* Sequence timing in seconds, written as one table because the whole point of
   a sequence is the relationship between its beats, and that is unreadable
   when the numbers are scattered through the code.

   Total is ~1.65s and the payoff — the two named pairs connecting — lands at
   1.1s. Longer than a UI animation is allowed to be, which is the marketing /
   explanatory exemption: the sequence is the explanation, not chrome on top of
   one. It is also fully skippable (see `hold`). */
const T = {
  leftStart: 0,
  stagger: 0.04,
  rightStart: 0.2,
  snapEdge: 0.9,       // the two named pairs connect
  snapDraw: 0.2,
  snapSettle: 1.05,    // and click into place
  restEdge: 1.35,      // everything else follows, quietly
  restStagger: 0.08,
  restDraw: 0.3,
};

/* Critically damped: nothing carried momentum into a node arriving, and
   overshoot on something that merely faded in reads as a toy. */
const NODE_SPRING = { type: "spring", bounce: 0, duration: 0.5 } as const;

/* Where a node sits before it settles. Never scale(0) — nothing in the real
   world appears from nothing. Full transform strings throughout, because
   Motion's x/y/scale shorthands are not hardware accelerated. */
const HIDDEN = "translateY(10px) scale(0.97)";
const SETTLED = "translateY(0px) scale(1)";

export function EntityGraph() {
  const reduced = usePrefersReducedMotion();

  const fieldRef = useRef<HTMLDivElement>(null);
  const leftRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const rightRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const pathRefs = useRef<(SVGPathElement | null)[]>([]);

  const [edges, setEdges] = useState<Edge[]>([]);
  const [snapped, setSnapped] = useState(false);
  const [focus, setFocus] = useState("");

  const controls = useRef<AnimationPlaybackControls[]>([]);
  const drawnRef = useRef<Animation[]>([]);
  const timer = useRef<number | undefined>(undefined);

  /* --- geometry ---------------------------------------------------------- */

  /* offsetLeft/offsetTop are layout positions and are unaffected by the
     transforms the sequence applies, so the edges can be measured while the
     nodes are still mid-flight without the lines drifting. */
  const measure = useCallback(() => {
    const field = fieldRef.current;
    if (!field || field.offsetWidth === 0) return;

    const next: Edge[] = [];
    for (let i = 0; i < PAIRS.length; i++) {
      const l = leftRefs.current[i];
      const r = rightRefs.current[i];
      if (!l || !r) return;
      next.push({
        x1: l.offsetLeft + l.offsetWidth,
        y1: l.offsetTop + l.offsetHeight / 2,
        x2: r.offsetLeft,
        y2: r.offsetTop + r.offsetHeight / 2,
      });
    }
    setEdges((prev) =>
      prev.length === next.length &&
      prev.every((e, i) => e.x1 === next[i].x1 && e.y1 === next[i].y1 &&
                           e.x2 === next[i].x2 && e.y2 === next[i].y2)
        ? prev : next);
  }, []);

  useLayoutEffect(() => {
    measure();
    const field = fieldRef.current;
    if (!field) return;
    const ro = new ResizeObserver(measure);
    ro.observe(field);
    return () => ro.disconnect();
  }, [measure]);

  /* --- the sequence ------------------------------------------------------ */

  /** Cancel everything and land on the settled state, immediately. */
  const finish = useCallback(() => {
    controls.current.forEach((c) => c.stop());
    controls.current = [];
    drawnRef.current.forEach((a) => a.finish());
    drawnRef.current = [];
    window.clearTimeout(timer.current);
    [...leftRefs.current, ...rightRefs.current].forEach((el) => {
      if (!el) return;
      el.style.opacity = "1";
      el.style.transform = SETTLED;
    });
    pathRefs.current.forEach((p) => { if (p) p.style.strokeDashoffset = "0"; });
    setSnapped(true);
  }, []);

  /* Depends on edges.LENGTH, not on the edge values, so a resize re-measures
     the lines without restarting the sequence under the reader. */
  useLayoutEffect(() => {
    if (reduced) { setSnapped(true); return; }
    if (edges.length !== PAIRS.length) return;
    const field = fieldRef.current;
    if (!field || field.offsetWidth === 0) return;   // stacked layout, no graph

    const paths = pathRefs.current;

    // Hide before paint. React committed the settled state; this is the only
    // moment the pre-state exists and the browser never sees the swap.
    [...leftRefs.current, ...rightRefs.current].forEach((el) => {
      if (!el) return;
      el.style.opacity = "0";
      el.style.transform = HIDDEN;
    });
    setSnapped(false);

    const q: AnimationPlaybackControls[] = [];
    const drawn: Animation[] = [];

    const settle = (el: Element, delay: number) =>
      q.push(animate(el, { opacity: 1, transform: SETTLED },
        { ...NODE_SPRING, delay }));

    /* Edges go through WAAPI rather than Motion. Drawing a stroke is
       predetermined motion with nothing to interrupt and no spring to carry,
       which is the case the Web Animations API is for — and Motion declines to
       write `strokeDashoffset` on an SVG path at all, which cost a build cycle
       to find. Keeping it here also means the whole edge animation runs off the
       main thread while React is still hydrating the rest of the page. */
    const draw = (p: SVGPathElement, delay: number, duration: number) =>
      drawn.push(p.animate(
        [{ strokeDashoffset: 1 }, { strokeDashoffset: 0 }],
        { duration: duration * 1000, delay: delay * 1000,
          easing: `cubic-bezier(${EASE_OUT.join(",")})`, fill: "both" }));

    PAIRS.forEach((_, i) => {
      const l = leftRefs.current[i];
      const r = rightRefs.current[i];
      if (l) settle(l, T.leftStart + i * T.stagger);
      if (r) settle(r, T.rightStart + i * T.stagger);
    });

    let rest = 0;
    PAIRS.forEach((pair, i) => {
      const p = paths[i];
      if (!p) return;
      if (SNAP.has(pair.brand)) draw(p, T.snapEdge, T.snapDraw);
      else draw(p, T.restEdge + rest++ * T.restStagger, T.restDraw);
    });

    // The click into place: a 3% overshoot on the four nodes the two accented
    // edges have just joined. Small enough to read as a magnet closing rather
    // than a box bouncing, and it is the one beat where bounce is earned,
    // because a snap is a momentum event.
    PAIRS.forEach((pair, i) => {
      if (!SNAP.has(pair.brand)) return;
      [leftRefs.current[i], rightRefs.current[i]].forEach((el) => {
        if (!el) return;
        q.push(animate(el, { transform: ["scale(1.03)", SETTLED] },
          { type: "spring", bounce: 0.2, duration: 0.4, delay: T.snapSettle }));
      });
    });

    timer.current = window.setTimeout(
      () => setSnapped(true), T.snapSettle * 1000);

    controls.current = q;
    drawnRef.current = drawn;
    return () => {
      q.forEach((c) => c.stop());
      drawn.forEach((a) => a.cancel());
      window.clearTimeout(timer.current);
    };
  }, [edges.length, reduced]);

  /* --- interaction ------------------------------------------------------- */

  /* Input is never locked out during the sequence. Reaching for a node while
     the graph is still assembling ends the sequence and hands over the
     finished graph, rather than making anyone wait for an animation to agree. */
  const hold = (brand: string) => {
    if (!snapped) finish();
    setFocus(brand);
  };

  const path = (e: Edge) => {
    const dx = Math.max((e.x2 - e.x1) * 0.42, 24);
    return `M ${e.x1 + 6} ${e.y1} C ${e.x1 + dx} ${e.y1}, ` +
           `${e.x2 - dx} ${e.y2}, ${e.x2 - 6} ${e.y2}`;
  };

  return (
    <figure className="lp-graph">
      <figcaption className="lp-graph-heads">
        <span className="lp-graph-head">
          What the board prints<span>posting order</span>
        </span>
        <span className="lp-graph-head lp-graph-head-right">
          What the register records
          <span>alphabetical, {FACTS.registerEntries} entries</span>
        </span>
      </figcaption>

      <div className="lp-graph-field" ref={fieldRef} data-focus={focus}
           onPointerLeave={() => setFocus("")}>

        <svg className="lp-edges" aria-hidden="true">
          {edges.map((e, i) => (
            <path key={PAIRS[i].brand}
                  ref={(el) => { pathRefs.current[i] = el; }}
                  className="lp-edge"
                  pathLength={1}
                  d={path(e)}
                  data-snap={SNAP.has(PAIRS[i].brand) && snapped}
                  data-live={focus === PAIRS[i].brand} />
          ))}
        </svg>

        {PAIRS.map((pair, i) => (
          <button key={pair.brand}
                  ref={(el) => { leftRefs.current[i] = el; }}
                  className="lp-node"
                  style={{ gridRow: i + 1, gridColumn: 1 }}
                  aria-label={`${pair.brand} on a job board is ${pair.register} on the register`}
                  data-live={focus === pair.brand}
                  onPointerEnter={() => hold(pair.brand)}
                  onFocus={() => hold(pair.brand)}
                  onBlur={() => setFocus("")}>
            {pair.brand}
          </button>
        ))}

        {PAIRS.map((pair, i) => (
          <button key={pair.register}
                  ref={(el) => { rightRefs.current[i] = el; }}
                  className="lp-node lp-node-right"
                  style={{ gridRow: REGISTER_ORDER.indexOf(pair.brand) + 1 }}
                  aria-label={`${pair.register} on the register is ${pair.brand} on a job board`}
                  data-live={focus === pair.brand}
                  onPointerEnter={() => hold(pair.brand)}
                  onFocus={() => hold(pair.brand)}
                  onBlur={() => setFocus("")}>
            {pair.register}
          </button>
        ))}
      </div>

      {/* Narrow screens: a crossing means nothing in a single column, so each
          pair becomes a two-line record instead of a diagram that lies. */}
      <div className="lp-graph-stack">
        {PAIRS.map((pair) => (
          <div key={pair.brand} className="lp-stack-pair"
               data-snap={SNAP.has(pair.brand)}>
            <span className="lp-stack-brand">{pair.brand}</span>
            <span className="lp-stack-register">{pair.register}</span>
          </div>
        ))}
      </div>
    </figure>
  );
}
