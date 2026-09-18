# App-token rewrite — design

Date: 2026-09-18. Status: approved in chat.

## Context

On 2026-09-18 the Cattolica Blackboard administrator registered application
`942d59a9-4d36-44f9-9a0e-28ca9c9fb233` on site `31321706-…`. The
client-credentials exchange now answers 200 with `scope: read write delete`,
tokens last one hour, and the token acts as Niccolò's own user
(`/v1/users/me` → `dmi99400.salvini`). Everything the server did with a
minutes-long token copied from DevTools is now done by the app itself.

Probed with the app token on 2026-09-18 (all 200 unless noted):
course memberships with `expand=user` (names, emails, studentId), announcements,
gradebook columns, column scores per user (`/v2/…/columns/{id}/users`),
attempts (no answers inside: score, status, dates only), assessment questions
(still opaque `QuestionBlock`), content children, calendar items, groups,
gradebook categories/schemas/periods, terms. `dataSources` → 403,
`/gradebook/users` → 404, attachments on a folder → 400.

## Goals

1. Drop the session-token path; client credentials are the only auth.
2. Fix tools whose output was wrong or empty under the old constraints.
3. Add the tools the new access makes possible.
4. Split the 656-line module so each file has one job.

Non-goals: writing question bodies through the API (Ultra returns opaque
blocks; the upload-questions file stays the way), course copy, admin routes.

## Layout

```
src/bb_mcp/
  client.py   auth (token cache, refresh once on 401), _request, _paged,
              BBML check, BlackboardError, config flags
  export.py   bb_export_test file builder (moved as-is)
  server.py   MCPServer + tools only
tests/        pure-logic tests (paging, bbml, export lines, grade join)
scripts/probe_writes.py   manual probe: hidden test + question POST,
              file upload, attachment on document; deletes what it makes
```

Tool names never change. `.mcp.json` launches `.venv/bin/python -m
bb_mcp.server` with `PYTHONPATH=src` (uv hides `.pth` files, CPython ≥3.11
skips hidden ones).

## client.py

- `BB_TOKEN` removed. `bb_config` reports `auth: client-credentials`,
  credentials present, write flags.
- Token cached until `expires_in - 60`. On a 401 with a cached token, drop the
  cache, fetch once, retry the request once. A second 401 raises.
- One `httpx.AsyncClient` per request, as today (simple, no lifecycle).
- Paging, BBML check, error mapping unchanged.

## Rewritten tools

- `bb_list_students(course_id, role="")` → `expand=user`, fields
  `userId, courseRoleId, user.name, user.contact.email, user.studentId`.
  Returns userId, name ("Given Family"), email, studentId, role. Optional
  role filter (Student, Instructor…).
- `bb_get_attempt` docstring: attempt carries score, status, readyToPost,
  created/attemptDate/modified — not the student's answers.
- `bb_whoami` → `/v1/users/me` plus tokeninfo scope.
- `bb_add_question` kept with "unverified" note until `probe_writes.py` says
  otherwise; if the probe answers 4xx the tool is removed in a follow-up.

## New tools

Read:
- `bb_get_course(course_id)` → id, courseId, name, term, availability,
  externalAccessUrl, created/modified.
- `bb_content_tree(course_id, max_depth=3)` → nested outline
  `{id, title, type, available, children:[…]}`; the Ultra outline panel is
  blank in the browser, this is the only way to see it.
- `bb_get_content(course_id, content_id)` → full item incl. `body`.
- `bb_list_announcements(course_id)` → id, title, created, draft/available,
  body.
- `bb_list_grades(course_id, column_id)` → per student: userId, name, email,
  studentId, status, score, possible, exempt. Joins column scores with
  memberships.
- `bb_gradebook_report(course_id)` → `{columns:[{id,name,possible}],
  rows:[{userId,name,email,studentId, scores:{columnId: score|null}}]}`.
  One paged call per column; fine for a course of ~50.

Write (need `BB_ALLOW_WRITES=1`):
- `bb_update_content(course_id, content_id, title="", body_html="")` →
  PATCH; body passes the BBML check.
- `bb_upload_file(course_id, parent_id, path, title="")` → `POST /v1/uploads`
  (octet-stream) then a child with handler `resource/x-bb-file`. Marked
  unverified until the probe runs.

## Error handling

Unchanged shape: `BlackboardError` with status and first 500 chars of the
body. 404 → "Not found". Write switches checked before any network call.

## Testing

- `pytest` on pure logic with a fake `_request`: paging stops at limit and
  when `nextPage` is absent; BBML rejects `<b>`; export lines; grade join
  handles a student with no score; content tree respects depth.
- Live smoke: `scripts/smoke.py` runs whoami, course, tree, students,
  gradebook report on `_170037_1` read-only. Run by hand.
