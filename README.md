# TeachAlike API

## Google authentication, email verification, and approval email

Public `POST /api/auth/register` now creates parent password accounts only.
New password accounts are saved with `email_verified=false`, receive a hashed,
single-use verification token, and cannot receive JWTs until
`POST /api/auth/verify-email` succeeds. Existing accounts are treated as
trusted legacy accounts during migration: they are marked verified, while roles,
password hashes, teacher approval status, books, narration, mini-games, and
children remain unchanged.

New/updated auth endpoints:

- `POST /api/auth/register` returns `requires_email_verification=true` and no tokens.
- `POST /api/auth/login` blocks unverified, banned, pending, or rejected accounts.
- `POST /api/auth/google` verifies a Google Identity Services ID token in Flask,
  links by Google `sub`, and never changes an existing role.
- `POST /api/auth/verify-email` verifies by raw token only; account id/email are ignored.
- `POST /api/auth/resend-verification` returns a generic response and rate-limits by IP/email.
- `POST /api/auth/refresh` repeats ban, email-verification, and teacher-approval checks.

Teacher approval uses the existing `teacher_applications` table. Approval from
non-approved to `approved` increments `approval_version`, creates one
`EmailDelivery` outbox event with key `teacher_approval:<teacher_id>:<version>`,
commits the approval, then attempts email delivery. Gmail outage does not roll
back the approval. Re-approving an already-approved teacher is idempotent and
does not create another email. If a teacher is later rejected and approved
again, the incremented approval version intentionally creates a new approval
event.

Run the MySQL migration before deploying the updated app:

```bash
mysql --host="$DB_HOST" --port="$DB_PORT" --user="$DB_USER" --password \
  "$DB_NAME" < migrations/20260807_google_email_auth.sql
```

Required Railway/backend variables:

- `FRONTEND_URL` and `FRONTEND_ORIGINS` set to the deployed frontend origin
  (`https://...` in production).
- `GOOGLE_AUTH_CLIENT_ID` from a Google Cloud OAuth Web client.
- `MAIL_TRANSPORT=gmail_api` with `MAIL_FROM_EMAIL`,
  `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, and `GMAIL_REFRESH_TOKEN`; use only
  the Gmail send scope: `https://www.googleapis.com/auth/gmail.send`.
- Or `MAIL_TRANSPORT=gmail_smtp` with `GMAIL_SMTP_USERNAME` and
  `GMAIL_SMTP_APP_PASSWORD`. Never use a normal Gmail password.

Google Cloud setup:

1. Create an OAuth Web client.
2. Add authorized JavaScript origins for localhost and production, for example
   `http://localhost:3000` and `https://your-frontend.up.railway.app`.
3. Set the same client ID as `GOOGLE_AUTH_CLIENT_ID` in Flask and
   `NEXT_PUBLIC_GOOGLE_AUTH_CLIENT_ID` in Next.js.
4. Invalid audience errors mean the frontend and backend client IDs differ.
   Origin errors mean the current browser origin is missing from Google Cloud.

Gmail setup:

1. Enable the Gmail API for the Google Cloud project.
2. Authorize only `gmail.send`.
3. Store the refresh token server-side in Railway; rotate it by issuing a new
   refresh token and replacing `GMAIL_REFRESH_TOKEN`.
4. Provider failures are stored as safe categories such as
   `TEMPORARY_PROVIDER_FAILURE`; raw provider responses and credentials are not logged.

Testing:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## Automatic book mini-games

The existing `MiniGame` system automatically prepares `quiz`, `word_puzzle`,
and `spelling` records after either an admin or approved teacher saves a book
with usable `text_content`. Kimi receives only saved book title, text, age
group, and reading level. Story text is clearly delimited as untrusted data;
child and account data is never sent. Generated JSON is schema-checked and all
excerpts, quiz answers, puzzle words, and spelling words must be grounded in the
saved book. Missing configuration, timeouts, malformed output, or provider
failure produce a deterministic playable fallback and do not fail book saving.

Generation is versioned with a SHA-256 fingerprint of title, text, age group,
reading level, and generator version. An unchanged fingerprint is reused on
GET; cover-only changes do not regenerate. Relevant edits mark the old rows
`stale` and create a new content version, preserving historical `GameResult`
links. Lifecycle values are `pending`, `generating`, `ready`, `fallback`,
`failed`, and `stale`. The current bounded synchronous workflow commits
`generating` before the Kimi call, so no database transaction remains open
while waiting for the provider. Move the provider step to a durable queue if a
future deployment needs multiple generation workers.

Endpoints:

- `GET /api/books/<book_id>/mini-games` lists the active standard games and
  upgrades a legacy book once.
- `GET /api/mini-games/<game_id>` returns child-safe content. Quiz answer keys,
  explanations, source excerpts, provider metadata, and puzzle answers are not
  exposed before grading. Spelling words are shown only for the existing
  memorisation stage.
- `POST /api/books/<book_id>/mini-games/regenerate` is rate-limited. Admins may
  use it for every book; only an approved, unbanned owning teacher may use it
  for their own book. Request book text and generator metadata are ignored.
- `GET /api/books/<book_id>/mini-games/generation-status` returns a child-safe
  status; safe provider diagnostics are included only for authorized managers.
- `POST /api/mini-games/<game_id>/results` accepts `child_id`, `answers`, and
  spelling `difficulty` where applicable. For quiz answers submit
  `question_id`, `selected_option_index`, and `hint_used`. The server loads the
  authoritative version, grades it, deducts five points for a correct hinted
  answer, caps points, saves answer data and content version, and updates the
  leaderboard. Client scores and answer keys are rejected.

Kimi uses NVIDIA NIM's server-only `KIMI_API_KEY` (or the shared
`NVIDIA_API_KEY`/`NVAPI_KEY`), `KIMI_MODEL`, endpoint, and timeout configuration.
The book's dominant Unicode script is used for supported language guidance,
with English as the documented uncertain-language fallback.

For an existing MySQL/Railway database, back up the database and run:

```bash
mysql --host="$DB_HOST" --port="$DB_PORT" --user="$DB_USER" --password \
  "$DB_NAME" < migrations/20260802_versioned_ai_mini_games.sql
```

The migration is idempotent and preserves existing games/results. Deploy the
backend migration before the frontend. Railway must provide the Kimi/NVIDIA
configuration; provider failure remains safe.
Run backend verification with:

```bash
python -m unittest discover -s tests -v
```

## Teacher registration and approval

`POST /api/auth/register` remains backward-compatible with the existing JSON
parent registration request. Send `account_type: "parent"` explicitly for new
clients; parent registration returns `201` and the client may continue with its
existing automatic-login flow. Public registration never accepts `admin`.

Teacher registration uses `multipart/form-data` with `account_type=teacher`,
`name`, `email`, `password`, `phone_number`, `address`, `teacher_type` (`school`
or `private_tuition`), the optional matching `school_name`/`tuition_name`, and a
required `professional_photo`. A valid submission creates the existing
`parents` account with role `teacher`, a one-to-one `teacher_applications` row, and
returns `202` without tokens.

Approval statuses mean:

- `pending`: awaiting an administrator; login and existing JWTs are blocked.
- `approved`: the teacher can authenticate normally.
- `rejected`: authentication is blocked; an optional safe rejection reason may
  be returned to that teacher.

Admins use these authenticated endpoints:

- `GET /api/admin/teachers?status=pending|approved|rejected`
- `GET /api/admin/teachers/<teacher_id>`
- `PATCH /api/admin/teachers/<teacher_id>/approve`
- `PATCH /api/admin/teachers/<teacher_id>/reject` with optional JSON `reason`
- Existing create, ban, unban, and delete teacher endpoints remain available.

Teacher phone numbers and addresses are emitted only by these admin responses
and the teacher's own `/api/parents/me` response. They are excluded from the
ordinary account serializer. Admin-created teachers and legacy teacher accounts
are approved automatically.

Professional photos use the existing validated upload pipeline (extension,
MIME, magic bytes, and `MAX_PROFILE_IMAGE_SIZE_MB`). The server derives
`teachalike/<account_id>/Image/Profile/profile`; clients cannot choose a folder
or public ID. Cloudinary metadata is written to the `assets` ledger as
`USER_PROFILE_IMAGE`, while MySQL stores only the URL/public ID and metadata—
never raw image bytes. Failed database transactions trigger Cloudinary cleanup.

## Book engagement

Engagement uses event records and the existing reading-session source of truth:

- A **view** is an authenticated non-admin opening book details. At most one
  `book_views` event is stored per account, book, and UTC date.
- A **unique viewer** is a distinct account with a view event for the book.
- A **read** is an existing `reading_sessions` row for a child and book.
- A **completed read** has a non-null `completed_at`; a **unique reader** is a
  distinct child represented in those sessions.
- A **like** is one unique `book_likes` row for a book and child.

Authenticated book endpoints:

- `POST /api/books/<book_id>/views` (daily-idempotent; admins return
  `recorded: false`)
- `GET /api/books/<book_id>/engagement?child_id=<accessible_child_id>`
- `PUT /api/books/<book_id>/likes/<child_id>`
- `DELETE /api/books/<book_id>/likes/<child_id>`

The optional `child_id` is access-checked before `liked_by_child` is returned.
Like/unlike operations are idempotent and never accept client-provided counts.
Admins use `GET /api/admin/book-analytics`, with optional `search`, `sort`
(`views`, `reads`, or `likes`), `page`, and `per_page`. It returns aggregate
counts only and never exposes individual child activity.

## Teacher-authored books

An authenticated teacher whose profile is currently `approved` can publish and
manage books. Pending, rejected, banned, parent, and child identities cannot use
the management operations. Ownership is always taken from the JWT; request
fields such as `created_by_account_id`, Cloudinary folders, and public IDs are
ignored and cannot change attribution.

- `POST /api/books` accepts JSON or `multipart/form-data` and an optional
  8–64 character `Idempotency-Key`. Multipart files are `cover_image`, up to
  eight `illustrations`, optional `video`, and optional `teacher_audio`.
- `GET /api/teacher/books` lists only the current teacher's books with aggregate
  views, reads, and likes.
- `GET /api/teacher/books/<book_id>` returns one owned editing record.
- `PATCH /api/teacher/books/<book_id>` updates one owned book and can replace
  managed media.
- `DELETE /api/teacher/books/<book_id>` deletes one owned book when it has no
  reading sessions.

Admins retain the existing unrestricted `/api/admin/books` operations. Every
safe book response uses the shared `created_by` and `created_by_label`
serializer. Teacher books display `Created by <teacher name>` without email,
phone, address, school, tuition, or approval data. Legacy/admin-created books
display `Created by TeachAlike`. The name snapshot remains after an account is
deleted, while the nullable owner foreign key uses `ON DELETE SET NULL`; the
book and its delivery media are not deleted with the teacher.

Teacher media uses the centralized, server-generated folders documented below.
Extension, MIME, signature, and configured size limits are checked before
upload. Image, video, and teacher-audio metadata is recorded in the existing
`assets` ledger; MySQL never receives raw bytes. New uploads are removed from
Cloudinary if their database transaction fails, and replaced/deleted managed
assets follow the existing confirmed cleanup path. Teacher-created books use
the same mini-games, narration, reading sessions, daily views, child likes, and
admin analytics as every other book.

### Centralized Cloudinary book storage

New book-owned images, videos, and official teacher narration use one persisted
canonical root:

```text
teachalike/Books/<teacher_id>_<teacher_name>/<book_id>_<book_name>/Images/cover
teachalike/Books/<teacher_id>_<teacher_name>/<book_id>_<book_name>/Images/picture_01
teachalike/Books/<teacher_id>_<teacher_name>/<book_id>_<book_name>/Video/video_01
teachalike/Books/<teacher_id>_<teacher_name>/<book_id>_<book_name>/Teacher_voice_audio/voice_audio_teacher
```

Admin/legacy books use `teachalike/Books/TeachAlike/<book_id>_<book_name>`.
Names are sanitized server-side and IDs prevent collisions. The saved
`books.asset_root_folder` is not exposed through book responses and remains
unchanged after renames. No old Cloudinary asset is moved or deleted by this
schema change; profile images, voice profiles, and personalized narration keep
their existing account-based paths.

Official teacher audio is separate from personalized cloned narration. Only
the approved owning teacher or an admin may upload/replace it, and protected
playback is proxied through `GET /api/books/<book_id>/teacher-audio`.

## Teacher and engagement database migration

The new SQLAlchemy models are registered before `db.create_all()`. Railway's
existing pre-deploy command (`python -m app.database_setup`) creates missing
tables, renames the former `teacher_profiles` table without losing its rows,
backfills teacher accounts without applications as `approved`, and verifies
them for `/health/ready`.

For an existing MySQL database managed manually, apply the idempotent migration
before deploying the new application version:

```bash
mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" -p "$DB_NAME" \
  < migrations/20260805_repair_teacher_applications_schema.sql
```

The repair migration creates `teacher_applications`, or renames the former
`teacher_profiles` table when appropriate. It additively repairs missing
columns, indexes, uniqueness, and foreign keys, recovers rows after interrupted
deployments, and backfills legacy teachers. It never drops, truncates, recreates,
or deletes application data. Existing pending, approved, and rejected decisions
are preserved. If duplicate account rows prevent the required unique
relationship, deployment stops for manual review rather than deleting a row.
The earlier `20260801` migration remains the combined bootstrap for teacher
applications, book views, and book likes on a completely new database.

After that migration, apply the idempotent ownership migration:

```bash
mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" -p "$DB_NAME" \
  < migrations/20260802_teacher_book_ownership.sql
```

It adds nullable creator ownership/snapshot fields, description and update
timestamps, an ownership index, an idempotency uniqueness constraint, and the
`ON DELETE SET NULL` foreign key without changing existing books. It also
changes the asset-ledger owner FK to nullable `SET NULL` so surviving book
media remains traceable after teacher deletion; ordinary account assets are
still deleted by the transactional account-cleanup workflow. Railway's
pre-deploy compatibility initializer performs the equivalent schema checks, so
`/health/ready` and `db.create_all()` deployments continue to work. No new
environment variables are required.

For the centralized Cloudinary book root, then apply:

```bash
mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" -p "$DB_NAME" \
  < migrations/20260803_cloudinary_book_structure.sql
python -m app.database_setup
```

The SQL adds only the nullable canonical-root column. `database_setup`
backfills it with the same application sanitizer and saved creator records.
It does not call Cloudinary or move existing files. No new environment variable
is required.

Run the complete backend suite without external Cloudinary calls using:

```bash
python -m unittest discover -s tests -v
```

## Demo seed data

After configuring the database, create repeatable local demo data with:

```bash
python seed.py
```

The seed adds demo accounts, children, text-only books, mini-games, reading
progress, feedback, game results, and leaderboard entries. It does not create
voice profiles, narrations, Cloudinary assets, profile images, book images, or
videos. Running it again does not duplicate existing seed records.

## Pronunciation recognition and deterministic comparison

Feature 5 extends the existing reading-session flow; it does not introduce a
second session system. Microphone recordings are converted to mono 16 kHz WAV
with ffmpeg and transcribed server-side through NVIDIA ASR. The temporary source
and WAV files are removed in a `finally` block and raw recordings are not saved
in MySQL or Cloudinary.

`POST /api/reading-sessions/<session_id>/pronunciation-check` accepts
`paragraph_index` plus `transcript` (and still accepts the legacy
`sentence_index` key). The server always reloads the selected paragraph from
the saved `Book`; client-supplied paragraph text, scores, points, and comparison
data are ignored. The response keeps the existing fields and adds:

- `provider_accuracy`: the Groq reading score, or null when unavailable (`accuracy` remains a numeric backwards-compatible field);
- `text_match_accuracy`: correct aligned expected words / expected words × 100;
- `scoring_provider` and `scoring_model`;
- `comparison.summary`, aligned `comparison.tokens`, and `comparison.practice_words`;
- `attempt_id` and a positive-only `improvement` value when applicable.

The reusable `pronunciation_comparison_service` uses bounded word-level
Levenshtein alignment. It compares Unicode NFKC/case-folded keys, treats straight
and curly internal apostrophes as explicit equivalents, ignores punctuation for
matching, and preserves the original display token and character offsets. Every
expected token has zero-based paragraph, sentence, sentence-word, global-word,
and character locations. Insertions have `after_word_index` and
`before_word_index` anchors. Status values are `correct`, `substitution`
(`Heard differently`), `deletion` (`Skipped`), and `insertion` (`Extra word`).

This is transcript fidelity, not a phonetic diagnosis. NVIDIA currently returns
a transcript rather than word/phoneme pronunciation confidence, so the API and
UI never claim that a mismatch proves mispronunciation. Accent, microphone
quality, or background noise may affect the detected words. Groq provides the
existing separate reading score; if it is unavailable, `scoring_provider` is
`local-fallback`, `scoring_model` and `provider_accuracy` are null, and the
explicitly labelled compatibility fallback uses deterministic text-match
accuracy.

Each check creates a private `PronunciationAttempt`. Retrieve owned-session
history newest-first with:

```text
GET /api/reading-sessions/<session_id>/pronunciation-attempts
GET /api/reading-sessions/<session_id>/pronunciation-attempts?paragraph_index=0
```

Both endpoints require the existing JWT and child/session ownership check.
Attempts cascade with their reading session. `ReadingSession.progress_log`
continues to receive a compact compatibility entry. A locked session row plus
the existing award history ensures a paragraph can award leaderboard points
only once, while every retry stores a fresh comparison.

1. Install API dependencies: `pip install -r requirements.txt`.
2. Install `ffmpeg` and ensure `ffmpeg` is available on the server PATH.
3. Set these server-only variables:

   ```env
   NVIDIA_ASR_API_KEY=your-rotated-server-side-key
   NVIDIA_ASR_API_URL=https://1598d209-5e27-4d3c-8079-4751568b1081.invocation.api.nvcf.nvidia.com/v1/audio/transcriptions
   NVIDIA_ASR_LANGUAGE=en-US
   NVIDIA_ASR_REQUEST_TIMEOUT=45
   GROQ_API_KEY=your-groq-api-key
   GROQ_MODEL=openai/gpt-oss-120b
   PRONUNCIATION_RATE_LIMIT_ATTEMPTS=60
   PRONUNCIATION_RATE_LIMIT_WINDOW_SECONDS=3600
   ```

4. Start the Flask API with `python run.py`.

### Database migration and tests

`PronunciationAttempt` is imported by `app.models` before `db.create_all()`.
The project’s idempotent schema preparation therefore creates the
MySQL-compatible `pronunciation_attempts` table, its cascade foreign key, and
session/paragraph/created-at indexes exactly once. Before deployment run:

```bash
python -m app.database_setup
python -m unittest discover -s tests -v
```

On Railway the existing pre-deploy command runs the same schema preparation.
Back up production data first as usual; this release adds a table and does not
rewrite saved book text or existing progress logs. External NVIDIA, Groq, and
Cloudinary calls are mocked by the pronunciation tests.

## Railway deployment

Deploy this repository as the backend service and add a MySQL service in the
same Railway project. The included `Dockerfile` installs the Python
dependencies and ffmpeg. Railway runs database preparation once in a
pre-deploy container, then starts Gunicorn without repeating schema work.

Set these variables on the backend service:

```env
MYSQL_URL=${{MySQL.MYSQL_URL}}
JWT_SECRET_KEY=replace-with-a-new-random-secret-of-at-least-32-characters
FRONTEND_ORIGINS=https://your-project.vercel.app
```

Use Railway's variable-reference autocomplete because `MySQL` must match the
database service's exact name. A complete `MYSQL_URL` is preferred because it
keeps the host, port, username, password, and database name synchronized.
`MYSQL_PUBLIC_URL` and a MySQL `DATABASE_URL` are also accepted.

If a URL is unavailable, the backend uses Railway's individual variables
(`MYSQLDATABASE`, `MYSQLHOST`, `MYSQLPASSWORD`, `MYSQLPORT`, `MYSQLUSER`),
then falls back to local `DB_NAME`, `DB_HOST`, `DB_PASSWORD`, `DB_PORT`, and
`DB_USER` variables. A `*.proxy.rlwy.net` public TCP proxy is supported, but
the private `MYSQL_URL`/`MYSQLHOST` reference is normally faster for services
in the same Railway project.

The pre-deploy command creates and verifies all model tables before serving
traffic and fails with the exact initialization stage if the database is not
ready.

`railway.toml` runs `python -m app.database_setup` once before rollout. In the
Railway backend service, verify the identical command under **Settings → Deploy
→ Pre-deploy Command**; repository configuration and the service setting must
both say `python -m app.database_setup`. A successful pre-deploy log must contain
`Database schema preparation completed` before the new application revision is
allowed to serve traffic. The
web worker deliberately skips automatic table creation on Railway, even if an
old `AUTO_CREATE_TABLES=true` variable is still present. This keeps Gunicorn
startup fast and prevents duplicate schema work during restarts or scaling.
The configured start command is explicitly wrapped in `/bin/sh -c` because
Railway runs Dockerfile service overrides in exec form; the shell wrapper is
required for `exec` and runtime variable expansion such as `$PORT`.
The deployment health check uses `/health/ready`, so Railway only marks a
revision healthy when the database is reachable and the complete
`teacher_applications` schema is present. `/health` remains a lightweight
process-liveness endpoint.

Generate a unique JWT secret, for example with `openssl rand -hex 32`. Never
put database credentials, provider secrets, or JWT secrets in the frontend.
Railway supplies `PORT`; do not define it manually.

After deployment:

```text
GET https://your-backend.up.railway.app/health
GET https://your-backend.up.railway.app/health/ready
```

Railway uses `/health/ready` during deployment. Readiness returns HTTP 200 with
`"database": "ready"` when the database is reachable and its schema is
complete.

The browser records audio, uploads it to the authenticated `/api/reading-sessions/:id/pronunciation-transcript` endpoint, and then sends the returned transcript to the existing pronunciation scoring endpoint. Recordings are deleted from the server immediately after transcription.

## Kimi story mini-games

Every book gets quiz, word-puzzle, and spelling content grounded in its title,
reading level, and full story text. The complete saved book content is sent to
Kimi, and every Story Challenge contains 10 questions. Kimi creates child-friendly
activities through NVIDIA NIM. The API validates every answer, excerpt, and target
word against the saved book before storing content in `mini_games.content`.

Configure Kimi on the API server:

```env
KIMI_API_KEY=your-server-side-nvidia-key
KIMI_MODEL=moonshotai/kimi-k2.6
KIMI_API_URL=https://integrate.api.nvidia.com/v1/chat/completions
KIMI_REQUEST_TIMEOUT=120
```

`NVIDIA_API_KEY` or `NVAPI_KEY` can be used instead of `KIMI_API_KEY`. Legacy
games are upgraded the next time the book's mini-games are opened. If Kimi is
unavailable, a grounded deterministic fallback keeps the book playable and will
be replaced by Kimi once the key is configured.

## NVIDIA book generation

Admins can generate a book draft with `POST /api/admin/book-draft`. The API sends the request server-side to NVIDIA NIM's OpenAI-compatible chat completions endpoint; the NVIDIA key is never sent to the browser.

Configure the API server:

```env
BOOK_GENERATION_PROVIDER=nvidia
NVIDIA_API_KEY=your-server-side-nvidia-key
NVIDIA_MODEL=openai/gpt-oss-120b
NVIDIA_API_URL=https://integrate.api.nvidia.com/v1/chat/completions
NVIDIA_REQUEST_TIMEOUT=60
```

Example request (with an admin JWT):

```json
POST /api/admin/book-draft
{
  "age_group": "6-8",
  "reading_level": "beginner",
  "idea": "A small cloud learns how to help a thirsty garden."
}
```

Set `BOOK_GENERATION_PROVIDER=gemini` to keep using the existing Gemini draft generator instead.

## Voice-cloned book narration (ElevenLabs)

Book preview narrations are generated separately from reading sessions. A parent selects one of their ready voice profiles and requests a cached narration for that `(book, voice profile)` pair. Generated audio and source recordings are private authenticated Cloudinary resources; after an ownership check, the API streams the signed resource to the browser so private Cloudinary delivery never depends on cross-origin browser redirects.

Cloudinary storage uses database IDs and server-derived dynamic folders:

```text
teachalike/
└── <user_id>/Audio/
    ├── Voice_profiles/voice_profile_<voice_profile_id>
    └── Generated_Books_Audio/
        └── <book_id>_<sanitized_book_name>/
            └── voice_<voice_profile_id>_<book_id>_<generation_id>
```

Voice-profile rows are flushed before upload so their IDs become part of the
private public ID. Narrations use their own generation IDs, allowing distinct
versions without overwriting old audio. The existing background API still
returns the latest cached narration for the same book/voice pair. All SDK
calls run through `app/services/cloudinary_service.py`; folder construction
runs through `app/services/cloudinary_path_service.py`.

Completed narration uploads may store a validated language tag, while
background narration records preserve `ELEVENLABS_LANGUAGE_CODE` when it is
configured. Exact Cloudinary deletion succeeds only after the provider
confirms `ok` or `not found`; ambiguous results remain retryable in MySQL.

Registration does not create empty Cloudinary folders. The first asset upload
creates its logical `asset_folder`. Apply
`migrations/20260726_add_assets.sql` to existing MySQL deployments so normalized
Cloudinary metadata is recorded in the `assets` ledger.

Set these Cloudinary variables on the API service:

```env
CLOUDINARY_CLOUD_NAME=your-cloud-name
CLOUDINARY_API_KEY=your-api-key
CLOUDINARY_API_SECRET=your-api-secret
CLOUDINARY_ROOT_FOLDER=teachalike
CLOUDINARY_DELIVERY_TIMEOUT_SECONDS=60
CLOUDINARY_UPLOAD_TIMEOUT_SECONDS=180
GUNICORN_TIMEOUT=300
MAX_CONTENT_LENGTH_MB=1000
MAX_PROFILE_IMAGE_SIZE_MB=10
MAX_CHILD_IMAGE_SIZE_MB=10
MAX_VOICE_PROFILE_SIZE_MB=50
MAX_BOOK_AUDIO_SIZE_MB=250
MAX_BOOK_VIDEO_SIZE_MB=1000
```

Voice-profile uploads accept MP3, WAV, WebM, OGG, M4A, and MP4 audio up to
50 MB by default. MP3 MIME aliases used by desktop and mobile browsers are
accepted only after the extension and MP3 magic bytes are verified. The
frontend does not apply its general 60-second timeout to this workflow because
the API securely stores the sample and clones it with ElevenLabs before
responding.

See [`docs/cloudinary-assets.md`](docs/cloudinary-assets.md) for folder
mappings, supported formats, endpoints, cURL examples, replacement/deletion
rules, account cleanup, deployment setup, migration details, and test commands.

Set these server environment variables (the defaults are also in `.env.example`):

```env
ELEVENLABS_API_KEY=your-server-side-key
ELEVENLABS_MODEL_ID=eleven_multilingual_v2
ELEVENLABS_OUTPUT_FORMAT=mp3_44100_128
ELEVENLABS_MAX_CHARS_PER_CHUNK=4500
ELEVENLABS_REQUEST_TIMEOUT=120
```

When a user creates a voice profile, the API sends the private recording to ElevenLabs' Instant Voice Cloning endpoint and stores only the returned voice ID alongside the private Cloudinary sample. When a user requests a book narration, the API sends the book text to ElevenLabs in sentence-aware chunks, combines the returned MP3 files with ffmpeg, and stores the result as a private authenticated Cloudinary resource. The ElevenLabs key and Cloudinary credentials never reach the browser.

This code intentionally uses a one-worker in-process thread pool per Gunicorn process because the current deployment has no queue service. Jobs are lost when a web process restarts and are not coordinated across replicas. For production/scale, move `app.controllers.book_narration_controller._generate_narration` to a durable Celery or RQ worker backed by Redis, while retaining the same `BookNarration` status polling API.

After changing API environment variables, restart the Flask process. Existing voice profiles without an ElevenLabs ID are cloned lazily the first time their owner requests a narration.
