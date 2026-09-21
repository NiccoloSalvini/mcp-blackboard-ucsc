# mcp-blackboard-ucsc

MCP server for **Blackboard Learn Ultra** at Università Cattolica del Sacro Cuore.
Course outline, students, announcements, tests, gradebook and grading over the
official public REST API, as the instructor.

Not to be confused with the several "mcp-blackboard" projects on GitHub, which
implement the *blackboard architectural pattern* — shared memory between agents.
This one talks to the LMS.

## Auth

OAuth2 client credentials, nothing else. The application is registered at
[developer.anthology.com](https://developer.anthology.com/) and the Blackboard
administrator at Cattolica added its Application ID under *Admin → REST API
Integrations*, bound to the instructor's user (done 18 September 2026). So the
token acts as that user — `bb_whoami` answers with the instructor's account —
with scope `read write delete`, lives an hour, and the server fetches a new
one by itself: once when the cache runs out, and once more if a request
answers `401` mid-hour.

`.env` at the project root (git-ignored):

```
BB_BASE_URL=https://blackboard.unicatt.it
BB_APP_KEY=…
BB_APP_SECRET=…
BB_ALLOW_WRITES=0
BB_ALLOW_GRADE_WRITES=0
```

## Setup

```bash
uv sync                       # creates .venv; do NOT use `uv run` afterwards
cp .env.example .env          # fill in key and secret
PYTHONPATH=src .venv/bin/python -m pytest -q
PYTHONPATH=src .venv/bin/python scripts/smoke.py   # live, read-only
```

`.mcp.json` launches `.venv/bin/python -m bb_mcp.server` with `PYTHONPATH=src`.
That is on purpose: `uv run` re-hides the venv's `.pth` files on every sync and
CPython ≥ 3.11 skips hidden `.pth` files, which leaves the editable install
inert (`ModuleNotFoundError: No module named 'bb_mcp'`). `PYTHONPATH` goes
around it. Keep the checkout **outside iCloud Drive** — a venv under
`~/Desktop` gets evicted file by file and every import hangs.

## Layout

```
src/bb_mcp/client.py   token, requests, paging, BBML check
src/bb_mcp/export.py   the Upload Questions file
src/bb_mcp/sve.py      the SVE verbalizzazione template
src/bb_mcp/tools_more.py  second tool set (attendance, rubrics, release rules, groups…)
src/bb_mcp/server.py   the tools
tests/                 pure-logic tests with a fake API (no network)
scripts/smoke.py       read-only pass over a live course
scripts/probe_writes.py  tries the two unverified writes on a hidden folder, then deletes it
```

## Tools

**Account and course** — `bb_whoami`, `bb_config`, `bb_list_courses`,
`bb_get_course`, `bb_list_students` (name, email, student number, role;
optional `role` filter).

**Content** — `bb_content_tree` (the whole outline, nested, hidden items
included — the Ultra outline panel renders blank in a browser, this is how you
see it), `bb_list_contents`, `bb_get_content` (body included),
`bb_list_announcements`.

**Assessments and gradebook** — `bb_list_assessments`, `bb_list_questions`,
`bb_list_gradebook_columns`, `bb_list_grades` (one column, every student, with
names), `bb_gradebook_report` (every column × every student),
`bb_student_grades` (every column, one student), `bb_list_attempts`,
`bb_get_attempt`, `bb_needs_grading` (every submission still waiting for a
mark, with names), `bb_list_submission_files`, `bb_download_submission` (the
files a student handed in, saved locally).

**Writes** (`BB_ALLOW_WRITES=1`) — `bb_create_content`, `bb_update_content`,
`bb_delete_content`, `bb_set_availability`, `bb_upload_file`,
`bb_create_assessment`, `bb_prepare_exam` (test shell + upload file in one
go), `bb_add_question`, `bb_create_assignment` (homework: content + column +
points + due date in one POST), `bb_create_gradebook_column`,
`bb_update_gradebook_column`, `bb_post_announcement`,
`bb_update_announcement`, `bb_delete_announcement`.
Everything is created **hidden** unless asked otherwise.

**Grades** (`BB_ALLOW_GRADE_WRITES=1` as well) — `bb_grade_attempt` (mark one
submission; `post=True` releases it) and `bb_set_grade` (override the column
grade). Their own switch because a wrong mark reaches students directly.

**No network** — `bb_export_test`, `bb_export_gradebook_csv` (reads, then
writes a local CSV).

**Second set** (`src/bb_mcp/tools_more.py`, same switches):

- *Engagement* — `bb_review_status` (items opened per student),
  `bb_content_viewed` (one item, every student).
- *Attendance* — `bb_list_meetings`, `bb_create_meeting`, `bb_mark_attendance`
  (from userIds or student numbers; unmatched ids returned), `bb_attendance_report`.
- *Rubrics* — `bb_list_rubrics`, `bb_get_rubric`, `bb_create_rubric` (criteria ×
  levels grid), `bb_attach_rubric`, `bb_evaluate_rubric` (grade switch),
  `bb_rubric_evaluations`.
- *Adaptive release* — `bb_list_release_rules`, `bb_release_by_grade`,
  `bb_release_by_date`, `bb_release_to_users`, `bb_delete_release_rule`.
- *Exceptions* — `bb_get_exception`, `bb_set_exception` (due date / attempts for
  one student), `bb_delete_exception`.
- *Forum* — `bb_list_discussions`, `bb_discussion_messages`,
  `bb_create_discussion`, `bb_post_discussion_message`.
- *Calendar* — `bb_list_calendar`, `bb_create_calendar_item`, `bb_delete_calendar_item`.
- *Audit* — `bb_gradebook_log` (who changed which grade, when, from where),
  `bb_attempt_receipt`.
- *Course copy* — `bb_copy_course` (selected parts into an existing course).
- *Groups* — `bb_list_groups`, `bb_create_group_set` (set + groups + members),
  `bb_assign_content_to_groups`, `bb_list_group_attempts`, `bb_grade_group_attempt`.

**Verbalizzazione (SVE)** — `bb_sve_fill_excel`: Cattolica's exam
registration site (`secure.unicatt.it/sve`) has no API, but *Registra voti >
Caricamento voti Excel* exports a template of the enrolled students and imports
it back. The tool fills its *Voto Finale* column from one gradebook column,
matching by matricola (round half-up; ≥ 31 → "30 e lode"; < 18 → `not_passed`),
leaves unmatched students blank and lists both directions of mismatch. It also
separates the two reasons a row is blank: nobody sat the exam, or a submission
is still waiting to be marked — the second comes back as `submitted_not_yet_marked`
with a warning, because registering that row blank is the costly mistake here.
Upload, the *Errore* column and *Inoltro alla firma* stay manual. **The import
is only open while the appello is in "Registrazione voto"** — after *Inoltro
alla firma* SVE takes no file, so generate and import before sending it on. The template's own
config sheet (LIST_HEADER, LIST_CONTENT, VOTO…) is read, not hard-coded
positions. Checked against a real export (appello 288551, SSI428): layout read,
two of three marks filled, config sheet and the 15 data validations preserved.
Office-hours booking for exam review: `bb_create_group_set(...,
self_enroll=True, limit=3)` makes sign-up slots students pick themselves.

Not possible on Ultra via the public API: course messages (400), a custom
grade schema such as 18–30 e lode (`POST /gradebook/schemas` is Classic-only),
question bodies (see below).

Unverified on this site: `bb_add_question`, `bb_upload_file`,
`bb_create_assignment`, `bb_update_gradebook_column`, `bb_update_announcement`,
`bb_delete_announcement`, `bb_delete_content`, `bb_grade_attempt`. Run
`BB_ALLOW_WRITES=1 PYTHONPATH=src .venv/bin/python scripts/probe_writes.py`
once: it works in a hidden folder and a draft announcement, reports which
routes answer 2xx, and removes what it made. `bb_grade_attempt` is left to a
real hand-in on a hidden assignment.

### Homework, end to end

```
bb_create_assignment(course, "HW1", "<p>…</p>", parent_id=hw_folder, points_possible=10,
                     due="2026-10-15T22:59:00.000Z")            # hidden
bb_set_availability(course, content_id, True)                    # release
…students hand in…
bb_needs_grading(course)                                         # queue with names
bb_download_submission(course, attempt_id, "hw1/ada")            # PDFs, code
bb_grade_attempt(course, column_id, attempt_id, 8.5, feedback="<p>…</p>")  # posted
bb_export_gradebook_csv(course, "gradebook.csv")
```

## What the instance supports

`blackboard.unicatt.it` is Learn SaaS **4000.21.0**. Checked with the app token
on 18 September 2026 against the API reference for that build:

| Route | Used by | Status |
|---|---|---|
| `GET /v1/users/me` | `bb_whoami` | 200, the instructor |
| `GET /v3/courses/{id}` | `bb_get_course` | 200 |
| `GET /v1/courses/{id}/users?expand=user` | `bb_list_students`, grade joins | 200 with names and emails |
| `GET/POST /v1/courses/{id}/contents[/{cid}/children]` | content tools | 200 / 201 |
| `PATCH /v1/courses/{id}/contents/{cid}` | `bb_update_content`, `bb_set_availability` | 200 |
| `GET/POST /v1/courses/{id}/announcements` | announcements | 200 / 201 |
| `GET /v1/courses/{id}/assessments/{aid}/questions` | `bb_list_questions` | 200, **opaque** (see below) |
| `POST …/questions` | `bb_add_question` | unverified |
| `GET/POST /v2/courses/{id}/gradebook/columns` | columns | 200 |
| `GET /v2/…/columns/{cid}/users` | `bb_list_grades`, `bb_gradebook_report` | 200 |
| `GET /v2/…/columns/{cid}/attempts[/{aid}]` | attempts | 200 — score, status, dates; **no answers** |
| `PATCH /v2/…/columns/{cid}/users/{uid}` | `bb_set_grade` | untested |
| `PATCH /v2/…/columns/{cid}/attempts/{aid}` | `bb_grade_attempt` | in the reference, untested |
| `GET /v2/…/gradebook/users/{uid}` | `bb_student_grades` | 200 |
| `GET /v1/…/gradebook/attempts/{aid}/files[/{fid}/download]` | submission files | 200 (empty on a test attempt) |
| `POST /v1/courses/{id}/contents/createAssignment` | `bb_create_assignment` | in the reference (Ultra ≥ 3300.9), unverified |
| `PATCH/DELETE /v1/courses/{id}/announcements/{aid}` | announcement edits | in the reference, unverified |
| `DELETE /v1/courses/{id}/contents/{cid}` | `bb_delete_content` | in the reference, unverified |
| `PATCH /v2/…/gradebook/columns/{cid}` | `bb_update_gradebook_column` | in the reference, unverified |
| `POST /v1/uploads` (multipart, field `file`) + `resource/x-bb-file` | `bb_upload_file` | unverified |
| `GET /v1/courses/{id}/performance/contentReviewStatus` | `bb_review_status` | 200 |
| `GET /v1/courses/{id}/contents/{cid}/states/{uid}` | `bb_content_viewed` | 200 / 404 when never opened |
| `GET/POST /v1/courses/{id}/meetings[…]` | attendance | GET 200; POST unverified |
| `GET/POST /v1/courses/{id}/rubrics[…]` | rubrics | GET 200; POST unverified |
| `GET/POST …/contents/{cid}/adaptiveRelease/rules[…]` | adaptive release | GET 200; criterion body undocumented, unverified |
| `GET/PUT/DELETE …/gradebook/columns/{cid}/exceptions/users/{uid}` | exceptions | GET 200 (404 = none); PUT unverified |
| `GET/POST /v1/courses/{id}/discussions[…]` | forum | GET 200; POST unverified |
| `GET/POST /v1/calendars/items` | calendar | GET 200; POST unverified |
| `GET /v1/courses/{id}/gradebook/logs` | `bb_gradebook_log` | 200 |
| `GET /v1/courses/{id}/attemptReceipts/{rid}` | `bb_attempt_receipt` | 200 |
| `POST /v2/courses/{id}/copy` | `bb_copy_course` | unverified |
| `GET/POST /v2/courses/{id}/groups[/sets]…` | groups | GET 200; POST unverified |
| `GET /v1/courses/{id}/messages` | — | 400 "cannot be done on an Ultra course" |
| `/v1/dataSources` | — | 403 (admin only) |

There is **no `/assessments` collection** in the public API. A test is a content
item: `POST /contents` with `contentHandler.id = "resource/x-bb-asmt-test-link"`
(Learn ≥ 3900.98); the response carries `assessmentId` and `gradeColumnId`.

**Questions are opaque on Ultra.** List and single question return only `id`,
`position` and `questionHandler.type: "QuestionBlock"` — no text, no answers.
So a generated exam reaches Blackboard through **Test > + > Upload Questions**,
which takes a tab-delimited file. `bb_export_test` writes it:

```
bb_export_test(questions=[
  {"type": "MC",  "text": "OLS minimises …", "answers": [{"text": "…", "correct": true}, {"text": "…", "correct": false}]},
  {"type": "MA",  "text": "…", "answers": [...]},          # one or more correct
  {"type": "TF",  "text": "…", "correct": false},
  {"type": "ESS", "text": "…", "example": "model answer"}, # graded by hand
  {"type": "NUM", "text": "…", "answer": -5.34, "tolerance": 0.02},
  {"type": "FIB", "text": "The penalty in LASSO is the ___ norm.", "answers": ["L1", "l1"]},
], path="exams/first-intermediate.txt")
```

One question per line, no header, **at most 250 per file**, MC with exactly one
correct answer, tabs and newlines scrubbed. **Points do not travel in the file**:
every question arrives at 0 and gets its value in the test afterwards. Time
limit, attempts and results release are set in the same screen — the API does
not expose them either.

**Bodies are BBML, not HTML.** Content and announcement bodies take `p`,
`ul`/`ol`/`li`, `a` with `href`, `strong`, `em`, `br`, `div`, `span`, `sub`,
`sup`, `del`, `h4`–`h6`. `<b>` and `<i>` are rejected with a 400 that echoes
the body and names no tag, so the server checks first and names the offender.

**Attempts carry no answers.** The attempt object has score, status,
`readyToPost` and timestamps; a student's answers to an Ultra test are only in
the Ultra grading view.

Lessons that cost the most: a `401` to an unauthenticated probe says nothing
about whether the route exists, and a `404` with a valid token does not prove a
feature is off — read the reference for the build first.
