/**
 * Shared primitives.
 *
 * Density over decoration: compact rows, real information per screen, status as
 * small precise indicators rather than large empty cards. There are no charts
 * here on purpose — every pixel is a decision or the evidence for one.
 */
import { useEffect, useState } from "react";
import type { Validation } from "./api";

/* --- theme ---------------------------------------------------------------- */

type Theme = "light" | "dark" | "system";

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(
    () => (localStorage.getItem("theme") as Theme) || "system");

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    localStorage.setItem("theme", theme);
  }, [theme]);

  return { theme, setTheme };
}

export function ThemeToggle({ theme, setTheme }: {
  theme: Theme; setTheme: (t: Theme) => void;
}) {
  const options: [Theme, string][] = [
    ["light", "Light"], ["system", "Auto"], ["dark", "Dark"]];
  return (
    <div className="flex rounded-lg p-0.5 gap-0.5"
         style={{ background: "var(--bg-sunken)", border: "1px solid var(--border)" }}>
      {options.map(([value, label]) => (
        <button
          key={value}
          onClick={() => setTheme(value)}
          aria-pressed={theme === value}
          className="px-2.5 py-1 text-xs rounded-md transition-colors"
          style={theme === value
            ? { background: "var(--bg-raised)", color: "var(--text)",
                boxShadow: "var(--shadow)" }
            : { color: "var(--text-faint)" }}
        >{label}</button>
      ))}
    </div>
  );
}

/* --- atoms ---------------------------------------------------------------- */

export function Pill({ tone = "muted", children }: {
  tone?: "muted" | "ok" | "warn" | "stop" | "accent";
  children: React.ReactNode;
}) {
  const tones = {
    muted:  { background: "var(--bg-sunken)",   color: "var(--text-muted)" },
    ok:     { background: "var(--ok-soft)",     color: "var(--ok)" },
    warn:   { background: "var(--warn-soft)",   color: "var(--warn)" },
    stop:   { background: "var(--stop-soft)",   color: "var(--stop)" },
    accent: { background: "var(--accent-soft)", color: "var(--accent)" },
  } as const;
  return (
    <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[11px]
                     font-medium whitespace-nowrap"
          style={tones[tone]}>{children}</span>
  );
}

export function Button({ variant = "ghost", disabled, onClick, children }: {
  variant?: "primary" | "ghost" | "danger";
  disabled?: boolean;
  onClick?: () => void;
  children: React.ReactNode;
}) {
  const styles = {
    primary: { background: "var(--accent)", color: "var(--accent-text)",
               border: "1px solid transparent" },
    ghost:   { background: "var(--bg-raised)", color: "var(--text)",
               border: "1px solid var(--border)" },
    danger:  { background: "transparent", color: "var(--stop)",
               border: "1px solid var(--border)" },
  } as const;
  return (
    <button onClick={onClick} disabled={disabled}
            className="px-3 py-1.5 rounded-lg text-[13px] font-medium
                       transition-opacity disabled:opacity-40
                       disabled:cursor-not-allowed"
            style={styles[variant]}>{children}</button>
  );
}

export function Stat({ label, value, hint }: {
  label: string; value: React.ReactNode; hint?: string;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[11px] uppercase tracking-wider"
            style={{ color: "var(--text-faint)" }}>{label}</span>
      <span className="text-lg tnum leading-none"
            style={{ color: "var(--text)" }}>{value}</span>
      {hint && <span className="text-[11px]"
                     style={{ color: "var(--text-faint)" }}>{hint}</span>}
    </div>
  );
}

/* --- validations ---------------------------------------------------------- */

/**
 * The list of checks the system ran before allowing (or refusing) a change.
 *
 * This is the component that makes the app an ontology rather than a form. A
 * pass is quiet; a fail is loud; an override is loudest of all, because it is
 * the one a human talked the system out of and it carries their name.
 */
export function ValidationList({ validations }: { validations: Validation[] }) {
  if (!validations.length) return null;
  return (
    <ul className="flex flex-col gap-1">
      {validations.map((v) => {
        const overridden = v.overridden;
        const failed = v.result === "fail";
        const tone = overridden ? "warn" : failed ? "stop" : "ok";
        const mark = overridden ? "!" : failed ? "✕" : "✓";
        return (
          <li key={v.rule} className="flex items-start gap-2 text-[12px]">
            <span className="mt-[1px] w-4 text-center shrink-0"
                  style={{ color: `var(--${tone === "ok" ? "ok"
                    : tone === "warn" ? "warn" : "stop"})` }}>{mark}</span>
            <span className="flex-1">
              <span className="mono" style={{ color: "var(--text-muted)" }}>
                {v.rule}
              </span>
              {v.detail && (
                <span style={{ color: "var(--text-faint)" }}> — {v.detail}</span>
              )}
              {overridden && (
                <div className="mt-0.5 pl-0" style={{ color: "var(--warn)" }}>
                  overridden: “{v.override_reason}”
                </div>
              )}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

export function Panel({ title, right, children }: {
  title?: React.ReactNode; right?: React.ReactNode; children: React.ReactNode;
}) {
  return (
    <section className="rounded-xl overflow-hidden"
             style={{ background: "var(--bg-raised)",
                      border: "1px solid var(--border)" }}>
      {title && (
        <header className="flex items-center justify-between px-4 py-2.5"
                style={{ borderBottom: "1px solid var(--border)" }}>
          <h2 className="text-[13px] font-semibold">{title}</h2>
          {right}
        </header>
      )}
      {children}
    </section>
  );
}

/**
 * What you see when the API is down.
 *
 * The previous version said "API not reachable. Is uvicorn running?" — true,
 * and useless. The one moment someone sees this screen is the moment they least
 * want to go hunting for the command, so it is on the page with a copy button.
 */
export function ServerDown() {
  const [copied, setCopied] = useState(false);
  const cmd =
    'cd /d D:\\Adzuna && python -m uvicorn web.api.app:app --port 8000';

  return (
    <div className="px-5 py-8 flex flex-col gap-3 items-start">
      <span className="text-[13px] font-medium">The server isn’t running.</span>
      <span className="text-[12.5px]" style={{ color: "var(--text-muted)" }}>
        Nothing is wrong with your data — this window is just a view onto it.
        Your 9am tracker run is a separate scheduled task and is unaffected.
      </span>
      <div className="w-full flex items-stretch gap-2">
        <code className="flex-1 px-3 py-2 rounded-lg text-[11.5px] overflow-x-auto
                         whitespace-nowrap"
              style={{ background: "var(--bg-sunken)",
                       border: "1px solid var(--border)" }}>{cmd}</code>
        <Button onClick={() => {
          navigator.clipboard?.writeText(cmd).then(
            () => { setCopied(true); setTimeout(() => setCopied(false), 1600); },
            () => undefined);
        }}>{copied ? "Copied" : "Copy"}</Button>
      </div>
      <span className="text-[11px]" style={{ color: "var(--text-faint)" }}>
        Or double-click <span className="mono">tools\Resolve.vbs</span>, which
        starts it and opens this window for you.
      </span>
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-4 py-10 text-center text-[13px]"
         style={{ color: "var(--text-faint)" }}>{children}</div>
  );
}
