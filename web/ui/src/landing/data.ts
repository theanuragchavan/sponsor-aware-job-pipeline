/**
 * Every fact on the landing page, in one place, each one traceable.
 *
 * Nothing here is illustrative. If a number or a name appears on the page it
 * appears in this file with the source file that proves it, because the page is
 * public and the whole argument is that the matching is honest.
 */

/** A brand → legal-entity pair, and which rule (if any) actually found it. */
export type Pair = {
  /** What a job board prints. */
  brand: string;
  /** What the Home Office register records. */
  register: string;
  /** Which deterministic rule proposed it, or null when neither could. */
  rule: "prefix" | "trading-as" | null;
};

/**
 * Source: README.md "The problem" table.
 *
 * `rule` is derived from sponsor_check.suggest_matches:
 *   - prefix at a word boundary: /^MONZO\b/ hits MONZO BANK.
 *   - trading-as: the register key splits on " T A ", so DELIVEROO indexes
 *     ROOFOODS LTD T A DELIVEROO.
 *   - Zego/EXTRACOVER and Speechmatics/CANTAB RESEARCH share no token at all.
 *     No rule reaches them and none is supposed to. A person supplies those.
 */
export const PAIRS: Pair[] = [
  { brand: "Monzo",        register: "MONZO BANK",                  rule: "prefix" },
  { brand: "Deliveroo",    register: "ROOFOODS LTD T A DELIVEROO",  rule: "trading-as" },
  { brand: "Faculty",      register: "FACULTY SCIENCE",             rule: "prefix" },
  { brand: "Zego",         register: "EXTRACOVER",                  rule: null },
  { brand: "Speechmatics", register: "CANTAB RESEARCH",             rule: null },
];

/** The two pairs the hero snaps together — the ones named in the README table. */
export const SNAP = new Set(["Monzo", "Deliveroo"]);

export const RULE_LABEL: Record<NonNullable<Pair["rule"]>, string> = {
  "prefix": "prefix at a word boundary",
  "trading-as": "trading-as",
};

/**
 * What difflib similarity scoring proposed when it was measured against the
 * real register. Source: README.md "The interesting decisions" and the
 * suggest_matches docstring in sponsor_check.py.
 *
 * No similarity scores are shown. The rejection was recorded as a list of
 * false positives, not as a threshold sweep, so a score here would be invented.
 */
export const REJECTED: { query: string; proposed: string }[] = [
  { query: "AMAZON",   proposed: "AMAZON CHARITABLE TRUST" },
  { query: "LEONARDO", proposed: "LEONARDO BELGIUM SA" },
  { query: "CATALYST", proposed: "CATALYST CAPITAL" },
];

/** Counts, each with the file that states it. Formatted, never computed here. */
export const FACTS = {
  /** web/api/registry.py — the register as loaded and indexed. */
  registerEntries: "121,188",
  /** web/api/store.py — rows in the tracker workbook. */
  jobRows: "3,308",
  /** README.md — distinct employers across those rows. */
  employers: "932",
  /** README.md — the measured miss rate that started all of this. */
  missed: "74",
  missedOf: "492",
} as const;
