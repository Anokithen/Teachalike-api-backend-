"""Async, cached narration generation. It is deliberately separate from reading sessions."""
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

from flask import current_app, jsonify, request
from flask_jwt_extended import current_user
from app.extensions import db
from app.middleware import can_access_book_narration, voice_profile_belongs_to_current_parent
from app.models.asset_model import Asset, GENERATED_BOOK_AUDIO
from app.models.book_model import Book
from app.models.book_narration_model import BookNarration, STATUS_FAILED, STATUS_PROCESSING, STATUS_READY
from app.models.voice_profile_model import STATUS_READY as VOICE_STATUS_READY, VoiceProfile
from app.services.cloudinary_service import (
    book_narration_public_id,
    delete_asset,
    signed_voice_delivery_url,
    stream_authenticated_audio,
    upload_book_narration,
)
from app.services.elevenlabs_service import ElevenLabsError, clone_voice_from_url, synthesize_narration


# Simple deployment-sized queue. Replace with Celery/RQ before running multiple
# replicas or requiring durable jobs; in-process jobs are lost on restart.
NARRATION_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="book-narration")
# The database row is durable, but the worker is intentionally in-process for
# this deployment. Keep the future so we can tell a live job from one orphaned
# by a web-process restart.
NARRATION_FUTURES = {}
NARRATION_FUTURES_LOCK = threading.RLock()


def _generate_narration(app, narration_id):
    """Worker entry point; always creates its own Flask app context/session."""
    output_path = None
    uploaded_metadata = None
    with app.app_context():
        try:
            narration = db.session.get(BookNarration, narration_id)
            if not narration or narration.status != STATUS_PROCESSING:
                return
            book, profile = narration.book, narration.voice_profile
            if not book or not profile or not book.text_content:
                raise ElevenLabsError("The book text or voice profile is no longer available.")
            if not profile.elevenlabs_voice_id:
                reference_url = signed_voice_delivery_url(
                    profile.cloudinary_public_id, profile.voice_sample_url, app.config
                )
                profile.elevenlabs_voice_id = clone_voice_from_url(
                    reference_url,
                    app.config,
                    profile_label=profile.label,
                    owner_name=profile.parent.name if profile.parent else None,
                    profile_id=profile.id,
                )
                db.session.commit()
            with tempfile.NamedTemporaryFile(prefix="teachalike-narration-", suffix=".mp3", delete=False) as output:
                output_path = output.name
            synthesize_narration(book.text_content, profile.elevenlabs_voice_id, output_path, app.config)
            with open(output_path, "rb") as audio_file:
                uploaded_metadata = upload_book_narration(
                    audio_file,
                    profile.parent_id,
                    profile.parent.name,
                    book.id,
                    book.title,
                    profile.id,
                    app.config,
                    generation_id=narration.id,
                    return_metadata=True,
                )
            narration.narration_audio_url = uploaded_metadata["secure_url"]
            narration.cloudinary_public_id = uploaded_metadata["public_id"]
            narration.error_message = None
            narration.status = STATUS_READY
            asset = Asset.query.filter_by(
                generation_id=narration.id,
                asset_category=GENERATED_BOOK_AUDIO,
                deleted_at=None,
            ).first()
            if asset is None:
                asset = Asset.from_cloudinary_metadata(
                    uploaded_metadata,
                    category=GENERATED_BOOK_AUDIO,
                    owner_user_id=profile.parent_id,
                    book_id=book.id,
                    voice_profile_id=profile.id,
                    generation_id=narration.id,
                )
                db.session.add(asset)
            db.session.commit()
        except ElevenLabsError as exc:
            db.session.rollback()
            narration = db.session.get(BookNarration, narration_id)
            if narration:
                narration.status = STATUS_FAILED
                narration.error_message = str(exc)[:500]
                db.session.commit()
        except Exception:
            db.session.rollback()
            if uploaded_metadata:
                try:
                    delete_asset(
                        uploaded_metadata["public_id"],
                        uploaded_metadata["resource_type"],
                        uploaded_metadata.get("delivery_type") or "authenticated",
                        config=app.config,
                    )
                except Exception:
                    app.logger.exception(
                        "Could not clean up failed narration upload asset_id=%s",
                        uploaded_metadata.get("asset_id"),
                    )
            narration = db.session.get(BookNarration, narration_id)
            if narration:
                narration.status = STATUS_FAILED
                narration.error_message = "Narration generation failed. Please try again later."
                db.session.commit()
        finally:
            if output_path and os.path.exists(output_path):
                os.remove(output_path)
            db.session.remove()


def _enqueue_narration(narration_id):
    app = current_app._get_current_object()
    with NARRATION_FUTURES_LOCK:
        existing = NARRATION_FUTURES.get(narration_id)
        if existing is not None and not existing.done():
            return False
        future = NARRATION_EXECUTOR.submit(_generate_narration, app, narration_id)
        NARRATION_FUTURES[narration_id] = future

        def forget_finished(done_future):
            with NARRATION_FUTURES_LOCK:
                if NARRATION_FUTURES.get(narration_id) is done_future:
                    NARRATION_FUTURES.pop(narration_id, None)

        future.add_done_callback(forget_finished)
        return True


def _narration_worker_is_live(narration_id):
    with NARRATION_FUTURES_LOCK:
        future = NARRATION_FUTURES.get(narration_id)
        return future is not None and not future.done()


def create_book_narration(book_id):
    book = db.session.get(Book, book_id)
    if not book:
        return jsonify({"error": "Book not found."}), 404
    data = request.get_json(silent=True) or {}
    voice_profile_id = data.get("voice_profile_id")
    profile = db.session.get(VoiceProfile, voice_profile_id) if voice_profile_id else None
    if not (voice_profile_belongs_to_current_parent(profile) or current_user.is_admin):
        return jsonify({"errors": ["voice_profile_id must reference a voice profile owned by this account."]}), 400
    if profile.status != VOICE_STATUS_READY:
        return jsonify({"errors": ["The selected voice profile is not ready yet."]}), 400
    if not (book.text_content or "").strip():
        return jsonify({"errors": ["This book has no text available for narration."]}), 400

    existing = (
        BookNarration.query.filter_by(
            book_id=book.id,
            voice_profile_id=profile.id,
        )
        .order_by(BookNarration.id.desc())
        .first()
    )
    if existing:
        # Preserve the unique cache row, but allow a failed transient/configuration
        # job to be retried after the server has been corrected.
        if existing.status == STATUS_FAILED:
            existing.status = STATUS_PROCESSING
            existing.error_message = None
            db.session.commit()
            _enqueue_narration(existing.id)
            return jsonify({"message": "Narration generation restarted.", "book_narration": existing.to_dict(), "existing": True}), 202
        if existing.status == STATUS_PROCESSING and _enqueue_narration(existing.id):
            # A processing row with no live future is an orphan left by a
            # restart. Re-queue it so the UI cannot remain stuck indefinitely.
            return jsonify({"message": "Narration generation restarted.", "book_narration": existing.to_dict(), "existing": True}), 202
        return jsonify({"book_narration": existing.to_dict(), "existing": True}), 200

    try:
        narration = BookNarration(
            book_id=book.id,
            voice_profile_id=profile.id,
            status=STATUS_PROCESSING,
        )
        db.session.add(narration)
        db.session.flush()
        narration.cloudinary_public_id = book_narration_public_id(
            profile.parent_id,
            profile.parent.name,
            book.id,
            book.title,
            profile.id,
            narration.id,
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal server error occurred."}), 500

    _enqueue_narration(narration.id)
    return jsonify({"message": "Narration generation started.", "book_narration": narration.to_dict()}), 201


def list_book_narrations(book_id):
    if not db.session.get(Book, book_id):
        return jsonify({"error": "Book not found."}), 404
    query = BookNarration.query.filter_by(book_id=book_id)
    if not current_user.is_admin:
        query = query.join(VoiceProfile).filter(VoiceProfile.parent_id == current_user.id)
    narrations = query.order_by(BookNarration.id.desc()).all()
    return jsonify({"book_narrations": [narration.to_dict() for narration in narrations]}), 200


def get_book_narration_status(narration_id):
    narration = db.session.get(BookNarration, narration_id)
    if not can_access_book_narration(narration):
        return jsonify({"error": "Book narration not found."}), 404
    if narration.status == STATUS_PROCESSING and not _narration_worker_is_live(narration.id):
        # Status polling is also a recovery path after a gunicorn/Railway
        # restart, when the in-memory executor and its future are lost.
        _enqueue_narration(narration.id)
    return jsonify(narration.to_dict()), 200


def get_book_narration_audio(narration_id):
    narration = db.session.get(BookNarration, narration_id)
    if not can_access_book_narration(narration):
        return jsonify({"error": "Book narration not found."}), 404
    if narration.status != STATUS_READY or not narration.narration_audio_url:
        return jsonify({"error": "This narration is not ready yet."}), 409
    try:
        return stream_authenticated_audio(
            narration.cloudinary_public_id,
            narration.narration_audio_url,
            current_app.config,
            request.headers.get("Range"),
        )
    except CloudinaryServiceError:
        return jsonify({
            "error": "Narration playback is temporarily unavailable."
        }), 503
