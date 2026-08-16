/**
 * The motion primitives this page needs, and nothing else.
 *
 * One rule runs through all of them: **the settled state is the only state
 * written to the stylesheet.** A pre-state never persists in CSS, because a
 * page whose content is hidden until an IntersectionObserver fires is a page
 * that shows nothing when the observer does not fire — a stalled callback, a
 * screenshot tool, an old browser, a scroll position restored past the trigger.
 * Motion supplies the pre-state as the first keyframe of the animation itself
 * and it exists for exactly as long as the animation runs.
 */
import { useEffect, useLayoutEffect, useState } from "react";
import { animate, inView } from "motion";

/** Strong ease-out. The built-in curves are too weak for anything deliberate. */
export const EASE_OUT = [0.23, 1, 0.32, 1] as const;

/**
 * Live reduced-motion preference.
 *
 * It subscribes rather than reading once: someone can change the setting with
 * the tab open, and a page that ignores that decided its own animation mattered
 * more than the request not to see it.
 */
export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(() =>
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches);

  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const onChange = () => setReduced(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  return reduced;
}

type Kind =
  /** A row arriving: 8px of travel and a fade. */
  | "rise"
  /** A rule drawn left to right across a rejected match. */
  | "strike";

/**
 * Play a one-shot reveal over `[data-reveal]` descendants the first time the
 * container is on screen.
 *
 * Each descendant carries its own `data-delay` in milliseconds, so the ordering
 * lives next to the markup it orders instead of in a lookup table here.
 *
 * One-way on purpose. A reveal that re-runs every time you scroll past turns
 * the page into a slot machine, and this content is meant to be re-read.
 */
export function useRevealOnView(
  ref: React.RefObject<HTMLElement | null>,
  kind: Kind,
  amount = 0.3,
) {
  const reduced = usePrefersReducedMotion();

  useLayoutEffect(() => {
    const root = ref.current;
    if (!root || reduced) return;

    let stop: (() => void) | undefined;
    const controls: { stop: () => void }[] = [];

    stop = inView(root, () => {
      root.querySelectorAll<HTMLElement>("[data-reveal]").forEach((el) => {
        const delay = Number(el.dataset.delay ?? 0) / 1000;
        controls.push(kind === "rise"
          ? animate(el,
              { opacity: [0, 1],
                transform: ["translateY(8px)", "translateY(0px)"] },
              { duration: 0.28, delay, ease: EASE_OUT })
          : animate(el,
              { clipPath: ["inset(0 100% 0 0)", "inset(0 0 0 0)"] },
              { duration: 0.24, delay, ease: EASE_OUT }));
      });
      return () => {};   // never reverse; the reveal is one-way
    }, { amount });

    return () => { stop?.(); controls.forEach((c) => c.stop()); };
  }, [ref, kind, amount, reduced]);
}
