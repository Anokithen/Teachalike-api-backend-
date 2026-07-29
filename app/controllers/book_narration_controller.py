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
