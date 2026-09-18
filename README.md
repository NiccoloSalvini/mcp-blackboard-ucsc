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
`bb_list_attempts`, `bb_get_attempt`.

**Writes** (`BB_ALLOW_WRITES=1`) — `bb_create_content`, `bb_update_content`,
`bb_set_availability`, `bb_upload_file`, `bb_create_assessment`,
`bb_add_question`, `bb_create_gradebook_column`, `bb_post_announcement`.
Everything is created **hidden** unless asked otherwise.

**Grades** (`BB_ALLOW_GRADE_WRITES=1` as well) — `bb_set_grade`. Its own switch
because a wrong mark reaches students directly.

**No network** — `bb_export_test`.

Unverified on this site: `bb_add_question` and `bb_upload_file`. Run
`BB_ALLOW_WRITES=1 PYTHONPATH=src .venv/bin/python scripts/probe_writes.py`
once; it reports which of the two answers 2xx and removes what it made.

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
| `POST /v1/uploads` + `resource/x-bb-file` | `bb_upload_file` | unverified |
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
