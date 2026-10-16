---
name: cover-letter
description: Draft a fact-checked cover letter for a saved job (jobs/<id>). Use when the user asks for a cover letter, introductory letter or Motivationsschreiben for a job.
---

# Cover letter

Argument: a job id (folder under `data/jobs/`) or enough to find one (`.venv/bin/jobsearch list`).

## 1. Gather
- Read `data/jobs/<id>/posting.md`. If its front matter has `summary_only: true`, stop and ask
  the user to paste the full advert (save it with the web app or by editing posting.md).
- Read `data/jobs/<id>/notes.md` if present - the only allowed source of motivation/preferences.
- Read `data/profile/facts.yaml` and `data/profile/letter-rules.yaml`.

## 2. Map requirements to facts (show the user)
List the advert's main requirements. For each: the fact IDs that genuinely support it, or
**GAP**. Say plainly which requirements the user does not meet. Do not stretch a fact to cover a gap.
Check the work location first: office/hybrid presence is only possible within `commute_area` in
data/search/config.yaml. Required presence outside it is a blocker - put it at the top of the gaps and tell the user before drafting. Remote roles
open to where the user lives are fine. Never describe them as based anywhere but the location in
their sender's `address_lines` (letter-rules.yaml).
If notes.md is empty, ask the user what genuinely interests them about this role (one question),
or write the letter without a motivation sentence.

## 3. Draft `data/jobs/<id>/letter.draft.md`
```
---
profile: ch            # a key under `senders` in letter-rules.yaml
to: |
  Hiring Team
  <Company>
subject: Application for <exact advert title>
---
Dear Hiring Team,

<sentences, each ending with its tag: [F:ID] / [F:ID1,ID2] / [J] / [P]>

<each standard_closing entry's text from letter-rules.yaml, untagged, verbatim, one paragraph each>

Kind regards,

<sender name from letter-rules.yaml>
```
- 250-380 words. Plain, specific, concrete. First person, past tense for past roles.
- Every sentence tagged. Paraphrase facts without changing verb strength, scope, numbers or
  who did it. Prefer fewer, well-matched facts over many loosely related ones.
- Write in the advert's language (de/it/fr/en) if the user agrees; translate facts faithfully.
- Pick the `profile` whose sender details suit the role (the keys and their purpose are in letter-rules.yaml).
- `standard_closing` entries are the user's own words; copy them verbatim. Never edit or add to
  them yourself, and never reuse their wording (or its fact) elsewhere in the letter.

## 4. Mechanical check
Run `.venv/bin/jobsearch check <id>`. Fix every ERROR by rewording to match the facts (never by
editing facts.yaml). Re-run until PASS. Report WARN lines (needs-confirmation facts need a yes).

## 5. Claim-by-claim audit (show the user)
Write `data/jobs/<id>/letter.audit.md`: a table with each sentence, its cited fact statement,
and a verdict - `exact`, `faithful paraphrase`, or `STRONGER THAN SOURCE` (with why: verb,
scope, ownership, tense, implied numbers or seniority). Rewrite anything STRONGER until none remain.
Then show the user: the gap list, the audit, and the letter text (tags stripped).

## 6. Render only when the user approves
`.venv/bin/jobsearch render <id>` -> PDF + DOCX in the job folder. Set status `drafting` while
working, and remind the user to log the application (`/log-effort`) once actually sent.
