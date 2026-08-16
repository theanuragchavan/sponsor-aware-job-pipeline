/**
 * Resolve — landing page.
 *
 * Style direction: "register document". The page is dressed as a public record
 * being read, not a product being sold. One rule governs every glyph: sans is a
 * person writing prose, mono is a string a system recorded. That split is the
 * subject, not a typographic flourish, which is why the display line itself is
 * set in both faces.
 *
 * The argument runs in four beats and then stops. A long scroll would dilute
 * it, and the audience decides in about fifteen seconds:
 *
 *   1. hero      — the graph assembles and proves the problem exists
 *   2. mechanism — what actually finds the pairs, and the two it cannot
 *   3. rejected  — the approach that was measured and thrown out
 *   4. the tool  — one door
 *
 * Colour, spacing and both themes come from src/index.css. Nothing here forks
 * that system; landing.css adds one colour token and a type scale on top of it.
 */
import { useEffect, useState } from "react";
import { ThemeToggle, useTheme } from "../components";
import { EntityGraph } from "./EntityGraph";
import { MismatchTable } from "./MismatchTable";
import { RejectedFuzzy } from "./RejectedFuzzy";
import { FACTS } from "./data";
import "./landing.css";

export default function Landing() {
  const { theme, setTheme } = useTheme();

  // The synthetic-data disclaimer is true of the deployed demo and false of a
  // local instance running the real store. On a page whose whole argument is
  // that every claim on it is checkable, a line that is sometimes untrue costs
  // more than it saves. Undefined until known, so it renders nothing rather
  // than guessing.
  const [demo, setDemo] = useState<boolean | undefined>(undefined);
  useEffect(() => {
    fetch("/api/meta")
      .then((r) => (r.ok ? r.json() : null))
      .then((m) => setDemo(m ? m.mode === "demo" : undefined))
      .catch(() => undefined);
  }, []);

  return (
    <div className="lp">
      <header className="lp-header">
        <div className="lp-header-inner">
          <div>
            <h1 className="lp-wordmark" style={{ display: "inline" }}>Resolve</h1>
            <span className="lp-wordmark-note">
              employer identity against the Home Office sponsor register
            </span>
          </div>
          <ThemeToggle theme={theme} setTheme={setTheme} />
        </div>
      </header>

      <main className="lp-wrap">

        {/* 1 — the hero. The graph is the largest thing on the page and the
            copy sits under it, because the demonstration outranks the pitch. */}
        <section className="lp-hero">
          <p className="lp-display">
            <span>Monzo is not on the register.</span>{" "}
            <span className="lp-display-id">MONZO BANK</span> is.
          </p>
          <p className="lp-lede">
            A UK Skilled Worker visa makes a job you cannot legally take worse
            than no job at all, so every posting is checked against the Home
            Office register of licensed sponsors, where the two sides never
            agree on the name.
          </p>

          <EntityGraph />
        </section>

        {/* 2 — the mechanism. */}
        <section className="lp-section">
          <span className="lp-label">Worked examples</span>
          <div className="lp-measure">
            <h2 className="lp-h2">Three are reachable by rule. Two are not.</h2>
            <p className="lp-body">
              Two deterministic rules propose candidates and neither one ever
              decides. A prefix rule anchored at a word boundary, so{" "}
              <code className="mono">MONZO</code> reaches{" "}
              <code className="mono">MONZO BANK</code> but not{" "}
              <code className="mono">MONZOX</code>. And a trading-as rule,
              because the register writes the legal entity and then appends the
              brand it trades under.
            </p>
            <p className="lp-body">
              Zego and Speechmatics share no token at all with their register
              entry. No rule reaches those and none is meant to. A person
              supplies them, which is the whole design: the rules narrow the
              field and a human closes it.
            </p>
          </div>

          <MismatchTable />

          <div className="lp-callout">
            <p>
              <span className="lp-num">{FACTS.missed}</span> of{" "}
              <span className="lp-num">{FACTS.missedOf}</span> employers this
              pipeline had scored “not a sponsor” plausibly are sponsors, under
              a different legal name.
            </p>
          </div>
        </section>

        {/* 3 — the documented negative, given real weight. It is the part
            interviewers ask about, and it is the part that shows the matching
            was measured rather than assumed. */}
        <section className="lp-section">
          <span className="lp-label">Rejected</span>
          <div className="lp-measure">
            <h2 className="lp-h2">Fuzzy matching was tried and measured. It lost.</h2>
            <p className="lp-body">
              Similarity scoring is the obvious next move once you have seen the
              two rules miss Zego. It looked fine until it was run against the
              real register. This is what it came back with.
            </p>
          </div>

          <RejectedFuzzy />

          <div className="lp-measure" style={{ marginTop: "2rem" }}>
            <p className="lp-body">
              <code className="mono">AMAZON CHARITABLE TRUST</code> sits in the
              register a few lines from{" "}
              <code className="mono">AMAZON UK SERVICES</code>. A similarity
              score has no way to prefer one over the other, and it does not
              know that a charitable trust does not hire software engineers.
            </p>
            <p className="lp-body">
              <strong>
                Every false positive costs a person a decision.
              </strong>{" "}
              The queue is worked by a human, so the looser rule made it longer
              without making it better. Precision beats recall when a person
              pays for the errors. The scorer was removed and the two strict
              rules stayed.
            </p>
          </div>
        </section>

        {/* 4 — one door. */}
        <section className="lp-section">
          <span className="lp-label">The tool</span>
          <div className="lp-measure">
            <h2 className="lp-h2">Every decision lands in a log with a name on it.</h2>
            <p className="lp-body">
              <span className="lp-num">{FACTS.jobRows}</span> tracked postings
              across <span className="lp-num">{FACTS.employers}</span>{" "}
              employers, matched against{" "}
              <span className="lp-num">{FACTS.registerEntries}</span> register
              entries. Names nobody has ruled on wait in a queue. You confirm
              one; it is checked back against the register before it is
              accepted, then written to the store with your reason attached.
            </p>
          </div>

          <div className="lp-cta-row">
            <a className="lp-button" href="/app">
              Open the tool
              <span className="lp-button-arrow" aria-hidden="true">→</span>
            </a>
            {demo === true && (
              <span className="lp-cta-note">
                Runs on synthetic data. The companies in the tool do not exist
                and it is not the Home Office register.
              </span>
            )}
            {demo === false && (
              <span className="lp-cta-note">
                Running against the live register and a real tracker.
              </span>
            )}
          </div>
        </section>
      </main>

      <footer className="lp-footer lp-wrap">
        <p>
          Built for a real problem the author has. The five pairs above are from
          the live Home Office register of licensed sponsors; the counts are
          measured on the working store, not estimated.
        </p>
      </footer>
    </div>
  );
}
