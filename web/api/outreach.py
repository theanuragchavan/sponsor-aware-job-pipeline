"""Draft the first message to someone who might get you in the door.

Three rules are structural here rather than advisory, because each is a way
this feature could quietly do damage:

**It never asks for a referral.** The single best thing in the outreach
playbook is that the opening message asks for something small and specific
instead. A stranger can answer a question about their own job in two lines; a
referral request asks them to spend their reputation on someone they have never
met. `test_outreach.py` greps every generated draft for the ask, so this cannot
regress by accident.

**Every claim about him comes from `ABOUT` below**, which is transcribed from
`D:\\Resume\\CONTENT_MASTER.md` with the section noted. There is no model in
this path and nothing is generated — a template cannot invent a degree
classification, a job title or a metric, which is exactly why it is a template.

**Nothing is sent.** The draft goes to a file and to the screen. There is no
send step in this module, and there should never be one.

The hook is the interesting part. A message that opens with a real shared thing
gets read; one that opens with "I came across your profile" does not. So the
hook is chosen from the signals that actually matched on that person's own
published text, and the draft shows which words produced it, so he can throw it
out when the match is a coincidence. `location: "London"` matching "also in
London" is real. A bio mentioning Gujarat because they went there on holiday is
not, and only a human can tell the difference.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

#: Facts about him, and where each one is verified. Nothing outside this table
#: may appear in a draft. Keep the wording plain: it is his voice, and
#: voice-dna.md is explicit that a concrete thing stated flatly beats an
#: adjective.
ABOUT = {
    # CONTENT_MASTER §2 — the parenthesised form is the real UoN title; the
    # designation is applied at award, so "finishing" is the honest tense.
    "study": "I'm finishing an MSc in Computer Science (AI) at Nottingham",
    # Used when the hook has already named the university, so the sentence can
    # say "there" instead of saying "Nottingham" for the second time in a row.
    "study_there": "I'm finishing an MSc in Computer Science (AI) there",
    "study_short": "I'm on the MSc in Computer Science (AI) at Nottingham",
    "study_short_there": "I'm on the MSc in Computer Science (AI) there",
    # CONTENT_MASTER §2 — B.Tech, Gujarat Technological University.
    "bachelors": "I did my B.Tech at GTU",
    # CONTENT_MASTER §4 #1 — stated without the marks, which are nobody's
    # business in a first message, and without the metrics, which read as
    # boasting to a stranger.
    "project": ("my dissertation was a dialogue agent that reads whether "
                "someone is confused or frustrated mid-conversation and "
                "changes how it responds"),
}

#: The opening line, per signal, strongest first. `profile.SIGNALS` decides
#: which of these fired; this only decides how it reads.
#:
#: Two rules learned by reading the first draft of this out loud. **The hook
#: says nothing about him** — the sentence after it does that, and a hook that
#: also introduced him produced "I saw you were at Nottingham too, I'm doing an
#: MSc at Nottingham. I'm on the MSc at Nottingham and…". **No em dashes**:
#: voice-dna.md treats one as a pause tell, and three messages that all pause
#: in the same place read as generated, which is the one thing this must not do.
#:
#: The third element lists what the hook has already named, so the sentence
#: after it can say "there" instead of repeating the word.
#:
#: Only the university is treated this way. The same trick on the company name
#: produced "I'm on the MSc at Nottingham, and I've applied there" — which reads
#: as having applied to Nottingham. Naming the employer twice in a short message
#: is what a person would do anyway, so the hooks simply do not name it.
HOOKS: tuple[tuple[str, str, frozenset], ...] = (
    ("nottingham", "I saw you were at Nottingham too.", frozenset({"uni"})),
    ("gtu", "I noticed GTU on your profile. I did my B.Tech there too, so it "
     "caught my eye.", frozenset()),
    ("maharashtra",
     "Saw Maharashtra on your profile. I grew up in Gujarat with "
     "Maharashtrian family, so it stood out.", frozenset()),
    ("gujarat", "Saw Gujarat on your profile. That's where I studied, so it "
     "stood out.", frozenset()),
    ("marathi", "Noticed you speak Marathi. So do I.", frozenset()),
    ("gujarati", "Noticed you speak Gujarati. So do I.", frozenset()),
    ("hindi", "Noticed Hindi on your profile, same here.", frozenset()),
    ("london", "I noticed we're both based in London.", frozenset()),
)

#: Fallback when nothing matched. Deliberately about their work rather than
#: about them: with no shared signal, the only honest reason to be writing is
#: that you looked at what they built.
NO_SIGNAL_HOOK = "I came across your GitHub while reading up on the company."

#: The question, by what kind of role it is. It has to be answerable in two
#: lines from their own experience — a question that needs research is a
#: request for work, and gets ignored for the same reason.
QUESTIONS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("forward deployed", "forward-deployed", "solutions engineer",
      "solution engineer", "customer engineer", "field engineer"),
     "how much of the week actually ends up on the customer's side versus in "
     "the codebase"),
    (("machine learning", "ml engineer", "ai engineer", "data scientist",
      "research engineer"),
     "how much of the ML work is models versus the plumbing around them"),
    (("data engineer", "analytics", "platform"),
     "what breaks most often once the pipelines are somebody else's problem"),
)
DEFAULT_QUESTION = "what the first few months actually look like on your team"

#: Phrases that would turn this into the message the golden rule forbids. The
#: check runs on generated output rather than on the templates, because a
#: template edit is exactly how one would sneak back in.
ASK_PATTERNS = (
    r"\brefer(?:ral|ring|\b)", r"\bput me forward\b", r"\bvouch\b",
    r"\bpass (?:on |along )?my (?:cv|resume)\b", r"\bget me an interview\b",
    r"\bhire me\b", r"\bany (?:openings|vacancies)\b",
)

#: Enough room for a real sentence, and short enough for the note box on a
#: LinkedIn connection request.
NOTE_LIMIT = 300


@dataclass
class Draft:
    """One person, one job, two lengths, and the reasoning shown."""
    person: str
    company: str
    job_title: str
    hook_key: str
    hook_evidence: str
    note: str                     # LinkedIn connection note
    message: str                  # email / contact form
    channel: str
    warnings: list[str]

    def as_dict(self) -> dict:
        return {
            "person": self.person, "company": self.company,
            "job_title": self.job_title, "hook_key": self.hook_key,
            "hook_evidence": self.hook_evidence, "note": self.note,
            "note_length": len(self.note), "note_limit": NOTE_LIMIT,
            "message": self.message, "channel": self.channel,
            "warnings": self.warnings,
        }


#: A plausible given name: letters, optionally joined by a hyphen or an
#: apostrophe. No digits and no underscores, which is what separates "Priya"
#: and "Jean-Baptiste" from "xX_dev_Xx". `[^\W\d_]` is "word character that is
#: neither a digit nor an underscore", i.e. a letter in any script, so CJK and
#: Devanagari names pass.
_NAME_OK = re.compile(r"[^\W\d_]+(?:['-][^\W\d_]+)*\Z", re.UNICODE)


def _first_name(name: str) -> str:
    """The name to open with, or nothing.

    A mononym stays whole. A handle is dropped: "Hi xX_dev_Xx," is worse than
    "Hi," and instantly identifies the message as machine-assembled, which is
    the one impression the whole feature exists to avoid.
    """
    part = (name or "").strip().split(" ")[0]
    return part if part and _NAME_OK.match(part) else ""


def pick_question(job_title: str) -> str:
    low = (job_title or "").lower()
    for needles, question in QUESTIONS:
        if any(n in low for n in needles):
            return question
    return DEFAULT_QUESTION


def pick_hook(signal_keys, company: str) -> tuple[str, str, frozenset]:
    """The opening line, the signal that produced it, and what it already named."""
    keys = set(signal_keys or ())
    for key, template, said in HOOKS:
        if key in keys:
            return key, template.format(company=company, **ABOUT), said
    return "", NO_SIGNAL_HOOK, frozenset()


def _tidy(text: str) -> str:
    """Collapse the whitespace that templating leaves behind."""
    return re.sub(r"[ \t]+", " ", text).strip()


def check(text: str) -> list[str]:
    """Everything wrong with a draft, in his own terms.

    Runs the same banned-word list the application-pack lint uses, plus the
    referral-ask check that is specific to this feature. Reported rather than
    corrected: a warning he reads teaches him what to avoid, a silent fix does
    not.
    """
    warnings: list[str] = []
    low = text.lower()

    for pattern in ASK_PATTERNS:
        m = re.search(pattern, low)
        if m:
            warnings.append(
                f"asks for a referral ({m.group(0)!r}) — the first message "
                f"should ask a question instead")

    # voice-dna.md treats an em dash as a pause tell, and the first version of
    # these templates was full of them. Checked here rather than left to the
    # standalone lint, because that lint only runs on a saved file and this text
    # can reach the clipboard without ever being saved.
    if "—" in text or " -- " in text:
        warnings.append("em dash used as a pause (voice-dna.md)")

    try:
        import sys
        root = Path(__file__).resolve().parent.parent.parent
        lint_dir = root / ".claude" / "skills" / "application-pack" / "scripts"
        if str(lint_dir) not in sys.path:
            sys.path.insert(0, str(lint_dir))
        import voice_lint                                   # type: ignore

        for _line, term, label in (
                voice_lint.find_hits(text, voice_lint.BANNED, "BANNED")
                + voice_lint.find_hits(text, voice_lint.HEDGES, "HEDGE")
                + voice_lint.find_hits(text, voice_lint.WEAK_VERBS, "WEAK")):
            warnings.append(f"{label.lower()}: {term!r} (voice-dna.md)")
    except Exception:                                       # noqa: BLE001
        # The lint is a nicety. If the skill folder is not in this checkout the
        # draft is still worth having, and failing here would take the whole
        # panel down for a missing dev file.
        warnings.append("could not run the voice-dna lint in this checkout")

    return warnings


def draft(*, person_name: str, company: str, job_title: str,
          signal_keys=(), evidence: str = "", evidence_by_signal=None,
          channel: str = "") -> Draft:
    """Assemble both lengths for one person.

    `evidence_by_signal` maps a signal key to the profile text that triggered
    it. The hook is chosen here, so the evidence has to be selected here too:
    passing one pre-chosen string meant showing a person's bio as the reason a
    *location* matched.
    """
    company = (company or "").strip() or "the company"
    first = _first_name(person_name)
    greeting = f"Hi {first}," if first else "Hi,"
    hook_key, hook, said = pick_hook(signal_keys, company)
    question = pick_question(job_title)
    role = (job_title or "").strip()
    # Use a pronoun for anything the hook has already named, so two consecutive
    # sentences do not land on the same proper noun.
    study = ABOUT["study_there" if "uni" in said else "study"]
    study_short = ABOUT["study_short_there" if "uni" in said else "study_short"]

    # --- the connection note ------------------------------------------------
    # One hook, one line of who he is, one question. Anything else does not fit
    # and, at this length, anything else is padding.
    # Progressively shorter until one fits. A single fallback is not enough:
    # the longest hook and the longest real job title ("Forward Deployed
    # Software Engineer, New Grad") together overflow both of the first two.
    # What gets dropped is ordered by what the reader can recover for
    # themselves — they can look up which role, they cannot guess the question.
    candidates = (
        f"{greeting} {hook} {study_short}, and I've applied for the {role} "
        f"role at {company}. If you have a minute, I'd like to know "
        f"{question}.",

        f"{greeting} {hook} {study_short}, and I've applied at {company}. "
        f"Would you mind telling me {question}?",

        f"{greeting} {hook} {study_short}, and I've applied at {company}. "
        f"Could I ask {question}?",

        f"{greeting} {hook} I've applied at {company}. Could I ask "
        f"{question}?",
    )
    note = _tidy(candidates[-1])
    for candidate in candidates:
        if len(_tidy(candidate)) <= NOTE_LIMIT:
            note = _tidy(candidate)
            break

    # --- the longer version -------------------------------------------------
    # Four short paragraphs. The last one matters most: it gives them a way to
    # say nothing without it being awkward, which is what makes the ones who do
    # reply reply properly.
    message = "\n\n".join([
        greeting,
        hook,
        (f"{study}, and I've applied for the {role} role at {company}. "
         f"Before I get too far into it I'd rather hear about the work from "
         f"someone doing it than from the job page."),
        (f"The thing I keep wondering about is {question}. Anything you can "
         f"tell me would help, even a sentence."),
        "No problem at all if you're busy. Thanks either way,",
        "Anurag",
    ])

    shown = (evidence_by_signal or {}).get(hook_key) or evidence or ""

    return Draft(
        person=person_name, company=company, job_title=role,
        hook_key=hook_key, hook_evidence=shown,
        note=note, message=message, channel=channel,
        warnings=check(note + "\n" + message))


def write_file(d: Draft, drafts_dir: Path) -> Path:
    """Save the draft where he can edit it before sending it himself.

    The file is the deliverable. Nothing in this project sends anything, and
    the one-line rule at the top of the file is there so that a draft opened in
    a week still carries its own instructions.
    """
    drafts_dir = Path(drafts_dir)
    drafts_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-",
                  f"{d.company} {d.person}".lower()).strip("-")[:60]
    path = drafts_dir / f"{date.today().isoformat()}-{slug or 'draft'}.md"

    warn_block = ("\n".join(f"- {w}" for w in d.warnings)
                  if d.warnings else "- none")
    hook = (f"matched on: {d.hook_key} — from their own profile text "
            f"({d.hook_evidence})" if d.hook_key
            else "no shared signal matched; opens on their work instead")

    path.write_text("\n".join([
        f"# {d.person} at {d.company}",
        "",
        f"Role: {d.job_title}",
        f"Drafted: {date.today().isoformat()}",
        f"Reach them via: {d.channel or 'LinkedIn'}",
        f"Hook: {hook}",
        "",
        "**Read it before you send it, and send it yourself.** Check the hook "
        "is a real match and not a coincidence — a wrong one is worse than no "
        "message at all.",
        "",
        "## Short version (LinkedIn connection note)",
        "",
        f"{d.note}",
        "",
        f"*{len(d.note)} of {NOTE_LIMIT} characters.*",
        "",
        "## Longer version (email or their contact form)",
        "",
        d.message,
        "",
        "## Lint",
        "",
        warn_block,
        "",
        "## If they reply",
        "",
        "Answer the question they answered. Do not ask for a referral in the "
        "second message either — the ask lands once you have had an actual "
        "exchange, and by then they will often offer.",
        "",
    ]), encoding="utf-8")
    return path
