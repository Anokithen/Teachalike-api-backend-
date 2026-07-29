# Cloudinary asset storage

TeachAlike keeps Cloudinary credentials and path decisions on the Flask API.
Clients upload a file and related database IDs only; they cannot submit an
`asset_folder`, `folder`, `public_id`, owner ID, or Cloudinary path.

Cloudinary remains optional for unrelated API routes. A Cloudinary-backed
request returns a sanitized `503` response when the three credentials are not
configured.

## Configuration

Set these server-only values locally or on the Railway API service:

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

`MAX_CONTENT_LENGTH_MB` is Flask's request-wide ceiling and must be at least as
large as the largest per-asset limit. All size values must be positive
integers. Never expose these credentials through `NEXT_PUBLIC_*` variables or
commit a real `.env`.

On Railway, add the variables to the API service rather than the frontend
service. Restart the API after changing them.

## Dynamic folders and canonical paths

Registration creates only a MySQL user. It does not upload placeholders, call
the Cloudinary Admin API, create empty folders, or reserve prefixes. The first
upload creates its logical folder through Cloudinary's dynamic-folder
`asset_folder` option.

```text
teachalike/
└── {user_id}/
    ├── Audio/
    │   ├── Voice_profiles/
    │   │   └── voice_profile_{voice_profile_id}.{extension}
    │   └── Generated_Books_Audio/
    │       └── {book_id}_{sanitized_book_name}/
    │           └── voice_{voice_profile_id}_{book_id}_{generation_id}.{extension}
    ├── Image/
    │   ├── Profile/
    │   │   └── profile.{extension}
    │   └── Children_profile/
    │       └── {child_id}_{sanitized_child_name}/
    │           └── profile.{extension}
    └── Video/
        └── {admin_id}/
            └── {book_id}_{sanitized_book_name}/
                └── {safe_name}_{uuid}.{extension}
```

The compatibility cover/illustration uploader uses
`teachalike/{admin_id}/Image/Book_media`; it no longer uses the legacy
top-level `book_media/` path. Videos are never accepted by that unscoped
endpoint.

The path service lowercases names, replaces separators, rejects traversal
tokens, removes unsafe characters, bounds segments to 80 characters, and
falls back to `unnamed`. IDs—not emails or names—provide uniqueness.
Deterministic profile public IDs include the trusted canonical folder prefix
so `profile` cannot collide between accounts.

## Resource types and privacy

| Asset | Cloudinary resource type | Delivery |
|---|---|---|
| User/child profile image | `image` | `upload` |
| Voice-profile sample | `video` | `authenticated` |
| Generated narration | `video` | `authenticated` |
| Book video | `video` | `upload` |

Cloudinary represents audio as a `video` resource. The API performs an
ownership check, signs the authenticated resource server-side, and streams it
through the API with HTTP Range support. The browser is never redirected
cross-origin to private Cloudinary delivery. Signed URLs retain the stored file
extension so browsers can play WAV, WebM, M4A, OGG, MP4-audio, and MP3 assets.

## Validation and limits

The API validates file presence, extension, MIME type, magic bytes, configured
size, asset category, related records, ownership, and role before persistence.

- Images: JPG, JPEG, PNG, WebP.
- Audio: MP3, WAV, WebM, OGG, M4A, MP4.
- Video: MP4, WebM, MOV.

Voice samples are limited to 50 MB by default. Common browser MP3 MIME aliases
and generic `application/octet-stream` uploads are accepted only when the
filename extension and MP3 magic bytes also match. Voice upload requests may
take longer than ordinary API calls because Cloudinary storage and ElevenLabs
cloning are completed together; the dedicated Cloudinary timeout and Gunicorn
timeout above prevent the general request limit from cutting off valid larger
files.

Expected errors are `400` for missing/malformed input, `403` for denied
management, `404` for missing related entities, `413` for size limits, `415`
for media mismatch, `422` for business rules, `500` for database persistence,
and `503` for storage/provider failures.

## Endpoints

All endpoints require the existing JWT/role middleware.

- `POST /api/assets/profile-image`
- `POST /api/assets/children/{child_id}/profile-image`
- `POST /api/assets/voice-profiles`
- `POST /api/assets/books/{book_id}/narrations`
- `POST /api/admin/books/{book_id}/videos`
- `GET /api/assets/{asset_id}`
- `DELETE /api/assets/{asset_id}`
- `GET /api/books/{book_id}/assets`
- `GET /api/users/me/assets`

Older frontend routes remain compatible and delegate to the same workflows:

- `POST|DELETE /api/parents/me/profile-image`
- `POST|DELETE /api/children/{child_id}/profile-image`
- `POST|DELETE /api/voice-profiles`
- `POST /api/books/{book_id}/narrations` for background ElevenLabs generation
- `POST /api/admin/book-media` for pre-book cover/illustration images only

Example uploads (placeholder token only):

```bash
curl -X POST "$API_URL/api/assets/profile-image" \
  -H "Authorization: Bearer REPLACE_WITH_JWT" \
  -F "file=@profile.png"

curl -X POST "$API_URL/api/assets/voice-profiles" \
  -H "Authorization: Bearer REPLACE_WITH_JWT" \
  -F "file=@voice.wav" \
  -F "label=Story voice"

curl -X POST "$API_URL/api/assets/books/28/narrations" \
  -H "Authorization: Bearer REPLACE_WITH_JWT" \
  -F "file=@narration.mp3" \
  -F "voice_profile_id=12"

curl -X POST "$API_URL/api/admin/books/28/videos" \
  -H "Authorization: Bearer REPLACE_WITH_ADMIN_JWT" \
  -F "file=@introduction.mp4"
```

## Metadata, replacement, and deletion

Apply `migrations/20260726_add_assets.sql` before deploying these routes to an
existing MySQL database. The migration creates the `assets` ledger if absent,
adds ownership/entity foreign keys and indexes, and removes the old
one-narration-per-book/voice cache index. Fresh test databases use the same ORM
shape through `db.create_all()`.

The ledger stores the Cloudinary asset/public IDs, secure URL, resource and
delivery types, format, folder, original name, byte size, dimensions,
duration, status, relationships, and timestamps. Public responses never
contain credentials or raw SDK results.

Profile replacement uploads first, persists the new metadata/model URL, then
cleans an older differently named asset. Deterministic overwrites request CDN
invalidation. If the metadata commit fails after a deterministic overwrite,
the confirmed replacement is retained instead of deleting the only
deliverable bytes.

New non-replacement uploads are deleted if their database commit fails.
Generated narrations use the database generation ID and do not overwrite an
older generation.

`DELETE /api/assets/{id}` loads a database-owned row, hides cross-user assets,
checks voice-profile references, deletes the exact public ID with its stored
resource/delivery type, clears related model fields, releases `active_slot`,
and soft-deletes the ledger row. Cloudinary `not found` is treated as
idempotent success. A failed upstream deletion leaves metadata active for a
safe retry.

Account deletion snapshots every database-owned Cloudinary identity before
MySQL cascades run, then asynchronously deletes those exact assets and
ElevenLabs voice IDs. Legacy model references are included only when no ledger
row already owns that public ID. Cleanup never accepts or recursively deletes
a client-provided prefix.

## Tests

No test calls the live Cloudinary service:

```bash
python -m unittest discover -s tests -v
python -c "from app import create_app; app = create_app(); print('app import ok')"
```

The suite mocks the SDK and checks path construction, validation, ownership,
replacement/cleanup ordering, exact deletion, signed delivery, legacy
delegation, multiple narration generations, and sanitized provider errors.
