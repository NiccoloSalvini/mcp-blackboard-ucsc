# mcp-blackboard-ucsc

MCP server for **Blackboard Learn Ultra** at Università Cattolica del Sacro Cuore.
Course content, assessments, questions, gradebook and grading over the official
public REST API.

Not to be confused with the several "mcp-blackboard" projects on GitHub, which
implement the *blackboard architectural pattern* — shared memory between agents.
This one talks to the LMS.

## What the instance supports

Probed against `blackboard.unicatt.it` (Learn SaaS **4000.21.0**). Routes that do
not exist answer `404` without a token; these answer `401`, which is how we know
they are there:

| Route | Used by |
|---|---|
| `GET/POST /v1/courses/{id}/contents` | `bb_list_contents`, `bb_create_content` |
| `GET/POST /v1/courses/{id}/assessments` | `bb_list_assessments`, `bb_create_assessment` |
| `GET/POST /v1/courses/{id}/assessments/{aid}/questions` | `bb_list_questions`, `bb_add_question` |
| `GET/POST /v2/courses/{id}/gradebook/columns` | `bb_list_gradebook_columns`, `bb_create_gradebook_column` |
| `GET /v2/.../columns/{cid}/attempts` | `bb_list_attempts`, `bb_get_attempt` |
| `PATCH /v1/.../columns/{cid}/users/{uid}` | `bb_set_grade` |
| `GET /v1/courses/{id}/users` | `bb_list_students` |

## Getting a token — the part that needs someone else

The REST API does **not** accept a browser session cookie. Both
`/learn/api/public/v1/*` and the internal `/learn/api/v1/*` return
`401 API request is not authenticated` for a logged-in browser. A bearer token is
the only way in, and getting one takes two steps:

1. **You**: register an application at [developer.anthology.com](https://developer.anthology.com/).
   You get an Application ID, a key and a secret. Free, takes minutes.
2. **The Blackboard administrator at Cattolica**: adds that Application ID under
   *Admin → REST API Integrations*, bound to your instructor account, so the token
   acts with your permissions and no more.

Step 2 is the real gate. Until an admin does it, the key is inert.

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
