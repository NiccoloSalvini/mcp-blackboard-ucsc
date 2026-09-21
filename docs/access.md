# Getting REST access to your Blackboard

Everything here talks to Blackboard's public REST API, and that API will not
answer until an application of yours has been registered **on your
institution's Blackboard by an administrator**. There is no way around it: the
browser session does not authenticate the API, and a teacher account cannot
register the integration itself.

That registration is the only hard part of the setup, and it is not technical —
it is a request to an office. This page exists so you can make that request once,
correctly, with a message you can paste.

Two steps. The first takes five minutes and is yours. The second is the one
that takes days, because it waits on somebody else.

---

## Step 1 — register an application (you, 5 minutes)

1. Sign in at [developer.anthology.com](https://developer.anthology.com/) with
   any email. This is Anthology's developer portal, not your university's
   Blackboard, and creating an account there grants no access to anything.
2. Create an application. Give it a name you will recognise, e.g.
   *"Course tools — <your name>"*.
3. You get three values:
   - **Application ID** — a UUID like `942d59a9-…`. Not a secret: the
     administrator needs it, and it is useless without the other two.
   - **Key** and **Secret** — these *are* secrets. They go in your local `.env`
     as `BB_APP_KEY` and `BB_APP_SECRET`, and nowhere else. Never mail them.

At this point nothing works yet. The application exists in Anthology's portal
but your university's Blackboard has never heard of it.

---

## Step 2 — ask your Blackboard administrator (the slow part)

The person you need is a **Blackboard System Administrator** at your
institution. Usually that is not your faculty's Blackboard contact: faculty
contacts do teaching support and cannot see the Admin Panel. They are, however,
the right door to knock on — they forward it to whoever holds the role.

Send them this. Replace the two bracketed values.

> **Subject: Registration of a REST API Integration on Blackboard Learn**
>
> Good morning,
>
> I would like to request the registration of a REST API Integration on our
> Blackboard Learn instance, to use the documented public API on my own courses
> — reading course content, enrolments and the gradebook, and posting
> announcements and assignments, exactly as I already do through the web
> interface.
>
> The application is registered on Anthology's developer portal. The
> Application ID to register is:
>
> `[YOUR APPLICATION ID]`
>
> The integration should be bound to **my own user** (`[YOUR USERNAME]`), not to
> an administrator account: this way the token carries my permissions as an
> instructor and nothing more, it sees only the courses I teach, and it can do
> nothing I could not already do by hand.
>
> The steps, from the Blackboard documentation, are: *Administrator Panel →
> Integrations → REST API Integrations → Create Integration*, entering the
> Application ID above, selecting my user as the **Learn User**, and setting
> **Available**, **End User Access** and **Authorized To Act As User** to *Yes*.
>
> I remain available for any clarification, and happy to describe what the
> integration is for.
>
> Thank you,
> [YOUR NAME]

<details>
<summary>Italian version, to paste as-is</summary>

> **Oggetto: Registrazione di una REST API Integration su Blackboard Learn**
>
> Buongiorno,
>
> vorrei richiedere la registrazione di una REST API Integration sulla nostra
> istanza Blackboard Learn, per utilizzare l'API pubblica documentata sui miei
> corsi — lettura dei contenuti, degli iscritti e del registro, pubblicazione di
> annunci e compiti — esattamente come già faccio attraverso l'interfaccia web.
>
> L'applicazione è registrata sul portale sviluppatori di Anthology.
> L'Application ID da registrare è:
>
> `[APPLICATION ID]`
>
> Chiedo che l'integrazione sia associata **al mio utente** (`[USERNAME]`) e non
> a un'utenza amministrativa: in questo modo il token porta i miei permessi di
> docente e nulla di più, vede solo i corsi che insegno, e non può fare nulla che
> io non possa già fare a mano.
>
> I passaggi, dalla documentazione Blackboard, sono: *Pannello di
> amministrazione → Integrazioni → REST API Integrations → Create Integration*,
> inserendo l'Application ID qui sopra, selezionando il mio utente come **Learn
> User**, e impostando **Available**, **End User Access** e **Authorized To Act
> As User** su *Yes*.
>
> Resto a disposizione per qualsiasi chiarimento e per illustrare l'uso previsto.
>
> Grazie,
> [NOME]

</details>

**Why an administrator would say yes.** The integration grants no new access. It
is bound to one instructor's account and inherits exactly that account's
permissions: the same courses, the same rights, the same audit trail. It is the
mechanism Blackboard itself documents for institutional integrations, and it can
be revoked from the same screen at any time.

---

## Step 3 — check it worked

Fill in `.env` (see `.env.example`) and ask for a token:

```bash
PYTHONPATH=src .venv/bin/python scripts/smoke.py
```

The first line tells you who the token acts as. If that is your name, you are
done, and everything else in this repository will work.

### When it does not

| What you see | What it means |
|---|---|
| `400 invalid_grant: Application <id> not registered with site <site-id>` | The registration has not been done yet, or it was done on a different Blackboard instance. This is the normal state before Step 2 completes. |
| `401 API request is not authenticated` on every route | The key and secret in `.env` do not match the registered application. |
| Token works, but a course is missing | The integration is bound to a different user, or you are not enrolled as an instructor on that course. |
| `403` on `/v1/dataSources` and other system routes | Correct and expected: the token is an instructor's, not an administrator's. |

---

## At Università Cattolica

Niccolò's registration went through in three days, in September 2026, via the
faculty Blackboard contact who forwarded the request to the administrators. The
entry points, in order of usefulness:

1. Your **faculty's Blackboard contact** (listed on the teaching-innovation
   pages of the intranet). They do not hold the administrator role, but they
   forward it to whoever does.
2. The **iCatt ticket channel** — Home, *Le comunicazioni per te*, *Richiesta
   informazioni* — which is the official route and leaves a trace.

Do not ask the IAM / authentication desk: single sign-on is a different system
and they will route you back.

Once registered, `bb_whoami` answers with your own account and the token carries
`read write delete` within your courses.
