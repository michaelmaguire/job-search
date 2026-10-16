"""Mechanical fact-check of jobs/<id>/letter.draft.md.

Every sentence of the body must carry at least one tag:
  [F:ID] or [F:ID1,ID2]  claim backed by profile/facts.yaml
  [J]                    statement about the job/company, from posting.md
  [P]                    personal preference/motivation, from the job's notes.md
Salutation, closing and signature lines are exempt, as are lines that exactly match a
`standard_closing` entry in letter-rules.yaml: the user's own verbatim words, which skip
the banned-phrase list but must cite (`fact:`) a fact for anything they say about him.

Errors: untagged sentence, unknown fact ID, a number not present in the cited
sources, a banned phrase, a guarded verb (led, architected...) not present in
the cited sources, body over max_words.
Warnings: cites a needs-confirmation fact, [J] text that doesn't resemble the
posting, [P] without notes.md, too few fact citations.
"""
import re

import yaml

from .jobs import job_dir, split_front_matter
from .paths import FACTS, RULES

TAG_RE = re.compile(r"\s*\[(F:[^\]]+|J|P)\]")
NUM_RE = re.compile(r"\d+(?:[.,'’]\d+)*")
NUMBER_WORDS = {"two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
                "thirteen", "fifteen", "twenty", "thirty", "hundred", "hundreds", "thousand", "thousands",
                "million", "millions", "billion", "dozen", "dozens", "half", "double", "doubled", "triple",
                "tripled", "decade", "decades"}
SALUTATION_RE = re.compile(r"^(dear|hello|sehr geehrte|liebe|gentile|egregi|spettabile|madame|monsieur|bonjour)\b", re.I)
CLOSINGS = {"kind regards", "best regards", "regards", "sincerely", "yours sincerely", "yours faithfully",
            "mit freundlichen grüssen", "mit freundlichen grüßen", "freundliche grüsse", "beste grüsse",
            "cordiali saluti", "distinti saluti", "cordialement", "bien à vous"}
# Sign-offs like "Kindest regards," / "With warm wishes," / "Many thanks," (short lines only).
CLOSING_RE = re.compile(r"^(with )?((kind|kindest|best|warm|warmest|warm and|sincere|many|my|all the) )*"
                        r"(regards|wishes|thanks|best)$|^thank you( again)?$|^yours( sincerely| faithfully| truly)?$")
VERB_STEMS = {"led": "le(a)?d", "lead": "le(a)?d", "leading": "le(a)?d", "architected": "architect",
              "architecting": "architect", "designed": "design", "founded": "found", "managed": "manag",
              "invented": "invent", "inventor": "invent", "owned": r"\bown", "headed": r"\bhead",
              "built": "buil[dt]", "created": "creat", "shipped": "ship"}


def norm_num(s):
    return re.sub(r"(?<=\d)[,'’](?=\d)", "", s)


def load_facts():
    return {f["id"]: f for f in yaml.safe_load(FACTS.read_text(encoding="utf-8"))["facts"]}


def load_rules():
    return yaml.safe_load(RULES.read_text(encoding="utf-8"))


def sentences(body, sender_names, standard=()):
    """Yield (line_no, sentence) for claim-bearing text; skips exempt lines."""
    names = {n.lower() for n in sender_names}
    standard = {" ".join(x.split()).lower() for x in standard}
    for no, raw in enumerate(body.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        plain = TAG_RE.sub("", line).strip(" -*")
        low = plain.lower().rstrip(",")
        if (SALUTATION_RE.match(plain) and len(plain) < 60) or low in CLOSINGS or CLOSING_RE.match(low) \
                or low in names \
                or " ".join(low.split()) in standard:
            continue
        line = re.sub(r"^[-*]\s+", "", line)
        # split after sentence punctuation; a tag right after the full stop stays with its sentence
        for s in re.split(r"(?<=[.!?])\s+(?!\[)", line):
            if s.strip():
                yield no, s.strip()


def check(job):
    d = job_dir(job)
    facts, rules = load_facts(), load_rules()
    _, body = split_front_matter((d / "letter.draft.md").read_text(encoding="utf-8"))
    posting = (d / "posting.md").read_text(encoding="utf-8") if (d / "posting.md").exists() else ""
    notes = (d / "notes.md").read_text(encoding="utf-8") if (d / "notes.md").exists() else ""
    sender_names = [s["name"] for s in rules["senders"].values()]

    errors, warnings, cited = [], [], set()
    standard = [e if isinstance(e, dict) else {"text": e} for e in rules.get("standard_closing") or []]
    flat = " ".join(TAG_RE.sub("", body).split()).lower()
    for e in standard:
        text = " ".join(str(e["text"]).split())
        if text.lower() not in flat:
            warnings.append(f"standard closing missing: \"{text}\"")
            continue
        for fid in [e["fact"]] if e.get("fact") else []:
            if fid not in facts:
                errors.append(f"UNKNOWN FACT {fid}  in standard_closing (letter-rules.yaml)")
            else:
                cited.add(fid)
    standard = [str(e["text"]) for e in standard]

    def fact_text(fid):
        f = facts[fid]
        return f["statement"] + " " + " ".join(map(str, f.get("numbers", [])))

    for no, sent in sentences(body, sender_names, standard):
        tags = TAG_RE.findall(sent)
        text = TAG_RE.sub("", sent)
        where = f'line {no}: "{text[:70]}{"..." if len(text) > 70 else ""}"'
        if not tags:
            errors.append(f"UNTAGGED  {where}")
            continue
        sources = []
        for t in tags:
            if t == "J":
                sources.append(posting)
            elif t == "P":
                sources.append(notes)
                if not notes.strip():
                    warnings.append(f"[P] used but notes.md is empty/missing  {where}")
            else:
                for fid in (x.strip() for x in t[2:].split(",")):
                    if fid not in facts:
                        errors.append(f"UNKNOWN FACT {fid}  {where}")
                        continue
                    cited.add(fid)
                    sources.append(fact_text(fid))
        src = norm_num(" ".join(sources)).lower()

        for n in NUM_RE.findall(text):
            if norm_num(n) not in src:
                errors.append(f"NUMBER '{n}' not in cited sources  {where}")
        for w in re.findall(r"[a-z]+", text.lower()):
            if w in NUMBER_WORDS and w not in src:
                errors.append(f"NUMBER WORD '{w}' not in cited sources  {where}")
        for phrase in rules.get("banned_phrases", []):
            if re.search(r"\b" + re.escape(phrase.lower()) + r"\b", text.lower()):
                errors.append(f"BANNED '{phrase}'  {where}")
        for verb in rules.get("guarded_verbs", []):
            if re.search(rf"\b{verb}\b", text, re.I):
                if not re.search(VERB_STEMS.get(verb.lower(), re.escape(verb.lower())), src):
                    errors.append(f"VERB '{verb}' not supported by cited sources  {where}")
        if "J" in tags and posting:
            words = set(re.findall(r"[a-zäöüé]{6,}", text.lower()))
            if words and sum(w in posting.lower() for w in words) / len(words) < 0.3:
                warnings.append(f"[J] sentence shares few words with posting.md - is it really from the advert?  {where}")

    for fid in sorted(cited):
        if facts[fid].get("status") == "needs-confirmation":
            warnings.append(f"cites {fid} (needs-confirmation): {facts[fid].get('note', '').strip()}")
    if len(cited) < rules.get("min_fact_citations", 0):
        warnings.append(f"only {len(cited)} distinct facts cited")

    words = len(re.findall(r"\b\w+\b", TAG_RE.sub("", body)))
    if words > rules.get("max_words", 10**6):
        errors.append(f"body is {words} words (max {rules['max_words']})")
    return {"errors": errors, "warnings": warnings, "cited": sorted(cited), "words": words}


def report_text(result):
    lines = [f"{result['words']} words, {len(result['cited'])} facts cited: {', '.join(result['cited'])}"]
    lines += [f"WARN   {w}" for w in result["warnings"]]
    lines += [f"ERROR  {e}" for e in result["errors"]]
    lines.append("PASS" if not result["errors"] else f"FAIL ({len(result['errors'])} errors)")
    return "\n".join(lines)


def strip_tags(text):
    return TAG_RE.sub("", text)
