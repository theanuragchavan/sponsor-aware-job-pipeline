# ATS form shapes

What each ATS family's application form looks like from the outside, so that
recording a Cowork session for one employer produces a skill that works for the
next one. Research, not personal data: this describes public form markup.

## Workday — the one worth recording first

Widest ATS by UK employer count, and the only family where a single recording
plausibly generalises, because **Workday ships stable `data-automation-id`
attributes and every tenant inherits them.**

That is measured, not assumed. Two unrelated public repositories, written three
years apart, in different languages, against different employers, drive the form
through the same identifiers:

| Repo | Language | Last push | Employer targeted |
|---|---|---|---|
| `ubangura/Workday-Application-Automator` | Puppeteer / JS | 2026-06-19 | Leidos (`wd5`) |
| `ahdibiaymen/workday-application-automation` | Selenium / Python | 2023-10-01 | different tenant |

Both use `file-upload-input-ref`, `websitePanelSet-{n}`, `password`, `Add`.
Independent convergence on the same strings is the evidence that they are
Workday's, not one author's guess.

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

## Greenhouse, Lever, Ashby

Not yet surveyed. Expect the opposite property: these render employer-authored
field sets, so a recording is far less likely to transfer. Survey before
promising a second family.
