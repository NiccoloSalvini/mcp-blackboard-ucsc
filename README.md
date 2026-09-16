# mcp-blackboard-ucsc

MCP server for **Blackboard Learn Ultra** at Università Cattolica del Sacro Cuore.
Course content, assessments, questions, gradebook and grading over the official
public REST API.

Not to be confused with the several "mcp-blackboard" projects on GitHub, which
implement the *blackboard architectural pattern* — shared memory between agents.
This one talks to the LMS.

## What the instance supports

Against `blackboard.unicatt.it` (Learn SaaS **4000.21.0**), with a real
instructor token, and checked against the published API reference for that
exact build:

| Route | Used by | Status |
|---|---|---|
| `GET /v1/users/me` | `bb_whoami` | **200 confirmed** |
| `GET/POST /v1/courses/{id}/contents` | `bb_list_contents`, `bb_list_assessments`, `bb_create_content`, `bb_create_assessment` | **200 confirmed** on GET |
| `GET/POST /v1/courses/{id}/assessments/{aid}/questions` | `bb_list_questions`, `bb_add_question` | **200 on GET, but opaque** — see below |
| `GET/PATCH/DELETE .../questions/{qid}` | — (not wrapped yet) | in the reference |
| `GET/POST /v2/courses/{id}/gradebook/columns` | `bb_list_gradebook_columns`, `bb_create_gradebook_column` | **200 confirmed** on GET |
| `GET /v2/.../columns/{cid}/attempts` | `bb_list_attempts`, `bb_get_attempt` | **200 confirmed** |
| `PATCH /v1/.../columns/{cid}/users/{uid}` | `bb_set_grade` | untested |
| `GET /v1/courses/{id}/users` | `bb_list_students` | **200 confirmed** (memberships: `userId` only, no names) |

There is **no `/assessments` collection and no GET of a single assessment** in
the public API — the reference for 4000.21.0 lists only the `/questions`
sub-resource. An earlier revision of this server called `GET/POST
/courses/{id}/assessments` anyway and, when that answered `404`, concluded the
assessment tools were unusable here. Both halves of that were wrong: the route
never existed, and the questions route underneath it works fine.

A test is a content item. Since Learn 3900.98 you create one with `POST
/contents` and `contentHandler.id = "resource/x-bb-asmt-test-link"`; the
response carries `assessmentId` (for the questions routes) and `gradeColumnId`
(for the gradebook). `bb_list_assessments` and `bb_create_assessment` are now
built on that, and `bb_create_content` takes a `kind` so it can make folders.

**Questions are opaque on this build.** Both the list and a single question on a
real exam return only `id`, `position` and `questionHandler.type:
"QuestionBlock"` — no text, no type, no answers. Ultra tests are made of
question blocks the public API hands back as handles, not as content. So
`bb_list_questions` can count and order them and nothing more, and
`bb_add_question` still sends the Original-era shape (`questionType`,
`displayText`, `answers`) that nothing here has confirmed. It has not been run
against a live course and should not be until a safe course exists to try it on.

What the API does **not** expose, on any route: a test's time limit, attempts
allowed, or when results and feedback are released. Those are set in the Ultra
UI. The gradebook column does carry `attemptsAllowed` and `scoringModel`, which
is the nearest thing.

The lesson that cost the most: `401` to an unauthenticated probe means only
that the request was not authenticated. It is not evidence the route exists,
and `404` with a valid token is not evidence a feature is disabled — check the
reference for the build first.

## Getting a token — the part that needs someone else

The REST API does **not** accept a browser session cookie from outside the page.
Both `/learn/api/public/v1/*` and the internal `/learn/api/v1/*` return
`401 API request is not authenticated` for a logged-in browser. A bearer token is
the only way in, and getting one takes two steps:

1. **You**: register an application at [developer.anthology.com](https://developer.anthology.com/).
   You get an Application ID, a key and a secret. Free, takes minutes.
2. **The Blackboard administrator at Cattolica**: adds that Application ID under
   *Admin → REST API Integrations*, bound to your instructor account, so the token
   acts with your permissions and no more.

Step 2 is the real gate. Until an admin does it, the key is inert.

## Working before the admin gets to it

The Ultra web UI is itself a registered application, and the token it holds is a
normal bearer token for the same public API this server talks to. Set it as
`BB_TOKEN` and every tool works immediately, acting as the signed-in user:

```
BB_TOKEN=<token from a logged-in session>
```

Grab one in Chrome, logged into Blackboard: DevTools -> Network, filter
`tokeninfo`, reload a course page, copy `access_token` off the request URL.

It is short-lived — minutes, not hours: the Ultra client refreshes it as it
works, and a value copied earlier is usually dead by the time it is pasted. Grab
it and use it in the same minute. It is also Blackboard's own UI credential
rather than one issued to this tool. So it is a way to get work done
today, not the arrangement to settle on. `scripts/bb-get.sh` does the same thing
from the shell for a single read, without starting the server.

Verified working on `blackboard.unicatt.it` on 15 September 2026.

## Setup

```bash
uv sync
cp .env.example .env     # fill in BB_APP_KEY and BB_APP_SECRET
```

Then confirm auth before anything else:

```
bb_config    # what it is pointed at, what it may do
bb_whoami    # who the token acts as
```

## Write protection

Reads always work. Writes do not, unless you ask twice:

| Variable | Unlocks |
|---|---|
| `BB_ALLOW_WRITES=1` | create content, assessments, questions, gradebook columns |
| `BB_ALLOW_GRADE_WRITES=1` | `bb_set_grade` — posting marks to the official gradebook |

Grades get their own switch because they are the one output here that reaches
students directly and is awkward to walk back. Content created through
`bb_create_content` and `bb_create_assessment` is **hidden by default**
(`available=False`) so nothing appears to a class before you have looked at it.

## Questions: API or import?

Both work, and they suit different jobs.

- `bb_add_question` writes one question at a time into an existing assessment.
  Good for building an exam programmatically, and for questions generated from a
  script.
- A **tab-delimited upload** or a **QTI package** imports a whole pool at once
  through the web UI. Better for bulk. Note that QTI import supports only
  True/False, Multiple Choice, Multiple Answer, Fill in the Blank and Essay, and
  drops tags and categories.

## Tools

Read: `bb_whoami`, `bb_config`, `bb_list_courses`, `bb_list_students`,
`bb_list_contents`, `bb_list_assessments`, `bb_list_questions`,
`bb_list_gradebook_columns`, `bb_list_attempts`, `bb_get_attempt`

Write: `bb_create_content`, `bb_create_assessment`, `bb_add_question`,
`bb_create_gradebook_column`, `bb_set_grade`
