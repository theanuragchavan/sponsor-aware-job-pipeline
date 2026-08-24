# ATS form shapes

What each ATS family's application form looks like from the outside, so that
recording a Cowork session for one employer produces a skill that works for the
next one. Research, not personal data: this describes public form markup.

## Workday — the one worth recording first

Widest ATS by UK employer count, and the only family where a single recording
plausibly generalises, because **Workday ships stable `data-automation-id`
attributes and every tenant inherits them.**

That is measured, not assumed. Three unrelated public repositories, written
years apart, in three languages, against different employers, drive the form
through the same identifiers:

| Repo | Language | Licence | Employer targeted |
|---|---|---|---|
| `ubangura/Workday-Application-Automator` | Puppeteer / JS | none | Leidos (`wd5`) |
| `ahdibiaymen/workday-application-automation` | Selenium / Python | none | different tenant |
| `berellevy/job_app_filler` | TS / React extension | **BSD-3-Clause** | tenant-agnostic |

All three use `file-upload-input-ref`, `formField-*`, `websitePanelSet-{n}`.
Independent convergence on the same strings is the evidence that they are
Workday's, not one author's guess.

`ubangura` is **archived** as of this reading — still works, superseded by a
Chrome extension called SnapFill. Treat it as a fixed historical data point
rather than a maintained source.

`berellevy` is the strongest of the three and the only one carrying a licence,
so it is the only one we could lawfully borrow code from. See below.

### Classify by shape, not by an id list

The list of ids in the next section is how the first two repos work, and it is
the weaker method. `berellevy` does something better: it never enumerates ids
at all. It matches on the **`formField-` prefix plus the widget's structure** —

```
BOOLEAN_RADIO   .//div[starts-with(@data-automation-id,'formField-')]
                     [count(.//input[@type='radio']) = 2]
SINGLE_CHECKBOX same prefix, [count(.//input[@type='checkbox']) = 1]
MULTI_CHECKBOX  same prefix, [count(...) > 1]
TEXT_AREA       same prefix, [.//textarea]
MONTH_YEAR      same prefix, [Month input][Year input][not Day]
SIMPLE_DROPDOWN same prefix, [.//button[@aria-haspopup='listbox']]
```

Nine widget types, no employer-specific string anywhere. **This matters for the
Application Questions page.** The earlier claim below — that nothing on that
page is recoverable — is half wrong and worth stating precisely:

* the **question's meaning** is employer-specific and can never come from a
  recording. Unchanged, and it is why condition 8 exists.
* the **widget type** is fully recoverable, on any tenant, including that page,
  because the generated id still carries the `formField-` prefix and the shape
  is Workday's.

So a recorded skill can know "this is a two-option radio" on a page it has
never seen. That is what makes condition 9 mechanically decidable rather than a
judgement call: an open prose box is `TEXT_AREA`, detectable before it is
filled.

### The typed answer, and why ours is not typed yet

`berellevy` stores every saved answer under a composite key —
`{section, fieldType, fieldName, answer}` — and its lookup **filters on
`fieldType` and `section` before returning anything**. A stored paragraph can
never be offered to a radio button, and "Start date" under *Education* is a
different answer from "Start date" under *Work Experience*.

It also keeps two kinds of hit apart all the way to the screen: an exact index
lookup returns `matchType: 'exact'`, a fuzzy one returns
`matchType: 'Similar: <score>'`, and a human sees which it was before anything
is filled. **A near match is a different fact from a match.**

`data/screening.yml` declares a `type:` on every entry and
`autosubmit_gate.resolve_question` reads none of it — it returns
`("answer", id)` on the first substring hit, with no notion of match quality and
no type check. Probed against the live file on 2026-08-24, that answers
*"Do you have the right to work in the UK without requiring sponsorship?"* —
a yes/no radio whose truthful answer is **No** — with the `type: text`
right-to-work paragraph, and condition 8 **passes**. It answers a US
work-authorisation question with that same UK paragraph. Both are latent rather than live, because
`RECORDED_ATS` is empty and condition 1 fails first; they arm themselves the
moment a session is recorded.

Fix, when it is taken: filter on type the way this repo does, return a match
kind alongside the answer, and let condition 8 pass only on an exact match of
compatible type.

### The four fixed pages

A Workday application always walks these, in order, each identifiable before it
is filled:

1. `contactInformationPage` — `legalNameSection_firstName` / `_lastName`,
   `addressSection_addressLine1` / `_city` / `_postalCode` / `_countryRegion`,
   `phone-device-type`, `phone-number`, `email`
2. `myExperiencePage` — `workExperienceSection` with `workExperience-{n}` panels
   (`jobTitle`, `company`, `location`, `description`, `formField-startDate`,
   `formField-endDate`), `educationSection` (`formField-schoolItem`, `degree`,
   `formField-field-of-study`, `formField-firstYearAttended`,
   `formField-lastYearAttended`, `formField-gradeAverage`),
   `formField-skillsPrompt`, `websiteSection`, `file-upload-input-ref`
3. `voluntaryDisclosuresPage` — `previousWorker`, `agreementCheckbox`
4. `selfIdentificationPage` — `gender`, `ethnicity`, `hispanicOrLatino`,
   `veteranStatus`, disability

Navigation is always `bottom-navigation-next-button`. Dates are two inputs,
`dateSectionMonth-input` and `dateSectionYear-input`, never a single field.

### The page that is NOT on that list

There is a fifth page — the employer's own **Application Questions** — and it is
the reason condition 8 of the autosubmit gate exists. It is per-tenant, its
fields carry generated ids, and it is where sponsorship, notice period and
salary get asked. Nothing on it can be answered from a recording.

So the split is: **pages 1–4 are a recorded skill; the questions page is a
lookup against `data/screening.yml` or the run stops.** A recording that appears
to cover the questions page has memorised one employer's questions and will
answer the next employer's wrong.

### What these repos do that we must not

Both sign in, and `ubangura`'s creates the account when sign-in fails, reading a
plaintext password from `information.js`. Ours does neither: the account already
exists, the browser is already logged in, and no credential is ever typed by an
agent. Cowork drives a session a human opened.

Neither repository carries a licence, so no code is copied from either. What is
taken here is the *shape of the form*, which is a fact about Workday.

## Greenhouse — surveyed 2026-08-24, and the earlier guess was wrong

The note here used to say to expect the opposite property, on the reasoning that
Greenhouse renders employer-authored field sets so a recording would not
transfer. `berellevy` drives Greenhouse with no employer-specific string at all:

```
TEXT_FIELD      .//div[@class='field'][./input[@type='text']]
TEXTAREA        .//div[@class='field'][.//textarea]
MULTI_CHECKBOX  .//div[starts-with(@class,'field')][count(checkbox) > 1]
SIMPLE_DROPDOWN .//div[@class='field'][.//select][select2-container]
```

The employer authors the *questions*, not the *markup*. Structure transfers;
meaning does not. That is the same split as Workday, which makes it the general
rule rather than a Workday quirk — and it is the load-bearing assumption under
the whole recorded-skill idea.

**The trap: Greenhouse ships two incompatible DOMs.** The repo carries two full
driver sets, `greenhouse/` (classic, `div.field`, select2) and
`greenhouseReact/` (`div.text-input-wrapper`, `fieldset.checkbox`,
`button[aria-label="Toggle flyout"]`). Nothing in the URL distinguishes them. A
session recorded against one is worthless against the other, so a Greenhouse
recording must assert which variant it is looking at before it fills anything —
otherwise it silently matches nothing and reports success on an empty form.

## Lever, Ashby

Still not surveyed, and no longer safe to guess at either way.
