---
title: Resolve
emoji: 🔗
colorFrom: blue
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
short_description: Employer identity across the UK sponsor register
---

# Resolve

**This Space runs on synthetic data. The companies in it do not exist and it is
not the Home Office register.**

A UK Skilled Worker visa makes a job you cannot legally take worse than no job
at all, so every posting has to be checked against the Home Office register of
licensed sponsors. That sounds like a database join and is not, because the two
sides never agree on the name:

| A job board prints | The register records |
|---|---|
| `Monzo` | `MONZO BANK` |
| `Deliveroo` | `ROOFOODS LTD T A DELIVEROO` |
| `Faculty` | `FACULTY SCIENCE` |
| `Zego` | `EXTRACOVER` |
| `Speechmatics` | `CANTAB RESEARCH` |

Two deterministic rules propose candidates and neither ever decides. A human
confirms, the choice is validated against the register, written back to the
store, and recorded with a reason and a name. Fuzzy matching was tried and
rejected on measured evidence — it proposed `AMAZON → AMAZON CHARITABLE TRUST`.

The last two pairs above are reachable by no rule at all. The tool says so
rather than hiding them.

## What you can do here

Confirm an alias. The preview tells you exactly how many rows it will restamp
before you commit, you have to write down why, and the decision lands in an
append-only, hash-chained log with your name on it. **Your changes are real and
they reset when the Space goes to sleep.**

There is no login. Names on decisions identify, they do not verify — in a real
deployment the actor would be the SSO subject.

## Source

Code, tests and the real (non-synthetic) pipeline:
<https://github.com/theanuragchavan/sponsor-aware-job-pipeline>
