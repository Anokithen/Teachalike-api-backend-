# TeachAlike API

## Teacher registration and approval

`POST /api/auth/register` remains backward-compatible with the existing JSON
parent registration request. Send `account_type: "parent"` explicitly for new
clients; parent registration returns `201` and the client may continue with its
existing automatic-login flow. Public registration never accepts `admin`.

Teacher registration uses `multipart/form-data` with `account_type=teacher`,
`name`, `email`, `password`, `phone_number`, `address`, `teacher_type` (`school`
or `private_tuition`), the optional matching `school_name`/`tuition_name`, and a
required `professional_photo`. A valid submission creates the existing
`parents` account with role `teacher`, a one-to-one `teacher_profiles` row, and
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
tables, backfills teacher accounts without profiles as `approved`, and verifies
them for `/health/ready`.

For an existing MySQL database managed manually, apply the idempotent migration
before deploying the new application version:

```bash
mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" -p "$DB_NAME" \
  < migrations/20260801_add_teacher_approval_and_book_engagement.sql
```

The migration creates `teacher_profiles`, `book_views`, and `book_likes` with
foreign keys, cascade behavior, indexes, and uniqueness constraints, then
backfills legacy teachers. It does not reset or delete existing data. No new
environment variables are required.

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

## NVIDIA pronunciation recognition

Reading-session microphone recordings are converted to mono 16 kHz WAV with ffmpeg and transcribed server-side through NVIDIA's ASR endpoint. The returned transcript is then scored by the NVIDIA chat model against the target sentence. The NVIDIA key is never sent to the browser, and matching readings receive the existing leaderboard points. A local similarity fallback keeps scoring available during a temporary NVIDIA outage.

1. Install API dependencies: `pip install -r requirements.txt`.
2. Install `ffmpeg` and ensure `ffmpeg` is available on the server PATH.
3. Set these server-only variables:

   ```env
   NVIDIA_ASR_API_KEY=your-rotated-server-side-key
   NVIDIA_ASR_API_URL=https://1598d209-5e27-4d3c-8079-4751568b1081.invocation.api.nvcf.nvidia.com/v1/audio/transcriptions
   NVIDIA_ASR_LANGUAGE=en-US
   NVIDIA_ASR_REQUEST_TIMEOUT=45
   NVIDIA_PRONUNCIATION_API_KEY=your-rotated-server-side-key
   NVIDIA_PRONUNCIATION_REQUEST_TIMEOUT=20
   ```

4. Start the Flask API with `python run.py`.

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

`railway.toml` runs `python -m app.database_setup` once before rollout. The
web worker deliberately skips automatic table creation on Railway, even if an
old `AUTO_CREATE_TABLES=true` variable is still present. This keeps Gunicorn
startup fast and prevents duplicate schema work during restarts or scaling.
The configured start command is explicitly wrapped in `/bin/sh -c` because
Railway runs Dockerfile service overrides in exec form; the shell wrapper is
required for `exec` and runtime variable expansion such as `$PORT`.
The deployment health check uses `/health`, which verifies that the HTTP
worker is live without running a database query. `/health/ready` remains
available when database and schema readiness must be checked explicitly.

Generate a unique JWT secret, for example with `openssl rand -hex 32`. Never
put database credentials, provider secrets, or JWT secrets in the frontend.
Railway supplies `PORT`; do not define it manually.

After deployment:

```text
GET https://your-backend.up.railway.app/health
GET https://your-backend.up.railway.app/health/ready
```

Railway uses `/health` during deployment. Readiness returns HTTP 200 with
`"database": "ready"` when the database is reachable and its schema is
complete.

The browser records audio, uploads it to the authenticated `/api/reading-sessions/:id/pronunciation-transcript` endpoint, and then sends the returned transcript to the existing pronunciation scoring endpoint. Recordings are deleted from the server immediately after transcription.

## Gemini story word quizzes

Every book gets a quiz grounded in its title, reading level, and full story text. Gemini creates child-friendly multiple-choice questions that mix word meaning, context, and story understanding. The API validates that each answer and target word are grounded in the book before saving the quiz JSON in the existing `mini_games.content` field.

Configure Gemini on the API server:

```env
GEMINI_API_KEY=your-server-side-key
GEMINI_MODEL=gemini-2.5-flash
GEMINI_REQUEST_TIMEOUT=45
```

Legacy static quizzes are upgraded the next time the book's mini-games are opened. If Gemini is unavailable, a grounded deterministic fallback keeps the book playable and will be replaced by Gemini once the key is configured.

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
