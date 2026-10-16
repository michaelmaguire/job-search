---
name: log-effort
description: Record a job-search effort (application sent, interview, recruiter call, networking, rejection or offer) in efforts.csv. Use when the user reports something they did or heard back about.
---

# Log effort

`data/efforts.csv` is the official-style record (RAV form 716.007 fields; columns in
`data/README.md`). Accuracy matters more than speed.

1. Turn the user's description into one row per effort (an interview with two rounds = two rows).
   Link `--job-id` when the job exists (`.venv/bin/jobsearch list`); company/title/link are then
   filled in automatically.
2. For applications capture: method (electronic/letter/in-person/phone), workload, contact
   person/email if known, documents sent (letter PDF filename, CV version).
   For interviews: interview_with, interviewer_role, interview_mode, interview_time.
   For results: result + rejection_reason as given (quote the employer's reason, don't interpret).
3. Show the row(s) to the user and ask before writing anything not stated by them.
4. Write: `.venv/bin/jobsearch log --type ... --date YYYY-MM-DD --job-id ... [--field value ...]`
   This also advances the job's status.
5. If an email was the source, suggest saving it into the job folder as evidence.
