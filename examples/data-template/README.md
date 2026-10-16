# job-search-data

Private records for the job-search tools - keep this repo private.
Everything here is plain text so git diffs stay readable and LibreOffice can open it.

| Path | What |
|---|---|
| `profile/facts.yaml` | Evidence base. Every claim in a cover letter must cite an entry here. |
| `profile/letter-rules.yaml` | Sender details, word limit, banned phrases, guarded verbs. |
| `search/companies.yaml` | Companies to poll (`polled`) or check by hand (`manual`). |
| `search/config.yaml` | Title/location filters and relevance scoring for `jobsearch fetch`. |
| `search/blocklist.yaml` | Blocked sources (not fetched, adverts hidden) and blocked companies (hidden from every source), kept separately; edited on the Sources and Companies tabs. |
| `search/seen.txt` | URLs already fetched (so adverts are only added once). |
| `jobs/<id>/` | One folder per advert: `posting.md` (saved advert + status), `notes.md` (your notes and genuine motivation), `letter.draft.md` (tagged draft), `letter.check.txt`, rendered PDF/DOCX, and anything else sent or received. |
| `efforts.csv` | The job-search log: one row per thing you did. |
| `reports/` | Monthly RAV-style summaries produced by `jobsearch report`. |
| `cv/` | CV versions that were sent. |

## efforts.csv

One row per effort. The columns cover what the Swiss RAV form 716.007
("Nachweis der persönlichen Arbeitsbemühungen") and the AFG interview table asked for.

| Column | Values / meaning |
|---|---|
| `date` | YYYY-MM-DD |
| `type` | `application`, `spontaneous-application`, `recruiter-contact`, `networking`, `interview`, `follow-up`, `result` |
| `company` | employer (for recruiters: "Recruiter for Employer") |
| `job_id` | folder name under `jobs/`, if any |
| `job_title` | Stellenbezeichnung |
| `workload` | Pensum, e.g. `100%` or `80-100%` |
| `method` | `electronic`, `letter`, `in-person`, `phone` |
| `rav_assigned` | `yes` if assigned by RAV (Zuweisung RAV) |
| `contact_person`, `contact_email`, `contact_phone`, `contact_address` | Kontaktperson / E-Mail / Telefon / Adresse |
| `link` | advert or online-form URL |
| `result` | `open`, `interview`, `rejected`, `offer`, `hired`, `withdrawn` (Ergebnis) |
| `rejection_reason` | Absagegrund |
| `interview_with`, `interviewer_role`, `interview_mode`, `interview_time` | for `interview` rows |
| `documents` | files sent, e.g. `letter.pdf; raw/your-cv.pdf` |
| `notes` | free text |

A job's current state is its latest row. The monthly report lists the
application rows for that month, each with its latest result.
