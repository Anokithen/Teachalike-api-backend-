"""Background cleanup of external assets owned by deleted accounts."""

from concurrent.futures import ThreadPoolExecutor

from flask import current_app

from app.models.asset_model import Asset
from app.services.cloudinary_service import delete_asset
from app.services.elevenlabs_service import delete_voice


ACCOUNT_CLEANUP_EXECUTOR = ThreadPoolExecutor(
    max_workers=2,
    thread_name_prefix="account-asset-cleanup",
)


def collect_account_asset_refs(account):
    """Snapshot external asset IDs before SQLAlchemy cascades remove records."""
    cloudinary_assets = [
        {
            "public_id": asset.cloudinary_public_id,
            "resource_type": asset.cloudinary_resource_type,
            "delivery_type": asset.cloudinary_delivery_type,
        }
        for asset in Asset.query.filter_by(owner_user_id=account.id).all()
        if asset.cloudinary_public_id
    ]
    known_public_ids = {item["public_id"] for item in cloudinary_assets}

    def add_legacy(public_id, resource_type, delivery_type):
        if public_id and public_id not in known_public_ids:
            cloudinary_assets.append({
                "public_id": public_id,
                "resource_type": resource_type,
                "delivery_type": delivery_type,
            })
            known_public_ids.add(public_id)

    add_legacy(account.profile_image_public_id, "image", "upload")
    elevenlabs = []
    for child in list(account.children or []):
        add_legacy(child.profile_image_public_id, "image", "upload")
    for profile in list(account.voice_profiles or []):
        add_legacy(profile.cloudinary_public_id, "video", "authenticated")
        elevenlabs.append(profile.elevenlabs_voice_id)
        for narration in list(profile.narrations or []):
            add_legacy(
                narration.cloudinary_public_id,
                "video",
                "authenticated",
            )
    return {
        "cloudinary": cloudinary_assets,
        "elevenlabs": [item for item in elevenlabs if item],
    }


def delete_account_asset_refs(asset_refs, config, logger):
    """Delete a previously captured set of Cloudinary and ElevenLabs assets."""
    failures = []
    seen_cloudinary_ids = set()
    seen_elevenlabs_ids = set()

    def delete_cloudinary(reference):
        public_id = reference.get("public_id")
        if not public_id or public_id in seen_cloudinary_ids:
            return
        seen_cloudinary_ids.add(public_id)
        try:
            delete_asset(
                public_id,
                reference.get("resource_type") or "auto",
                reference.get("delivery_type") or "upload",
                config=config,
            )
        except Exception as exc:
            logger.exception(
                "Could not delete account Cloudinary asset public_id=%s",
                public_id,
            )
            failures.append(exc)

    def delete_elevenlabs(voice_id, label):
        if not voice_id or voice_id in seen_elevenlabs_ids:
            return
        seen_elevenlabs_ids.add(voice_id)
        try:
            delete_voice(voice_id, config)
        except Exception as exc:
            logger.exception("Could not delete %s", label)
            failures.append(exc)

    for reference in asset_refs.get("cloudinary", []):
        delete_cloudinary(reference)
    for voice_id in asset_refs.get("elevenlabs", []):
        delete_elevenlabs(voice_id, "an ElevenLabs voice clone")

    if failures:
        raise RuntimeError(
            "Some external account assets could not be deleted. Cleanup will need to be retried."
        )


def _cleanup_in_background(app, asset_refs):
    with app.app_context():
        try:
            delete_account_asset_refs(asset_refs, current_app.config, current_app.logger)
        except RuntimeError:
            current_app.logger.exception("Background account asset cleanup did not finish")


def schedule_account_asset_cleanup(asset_refs):
    """Queue external cleanup without blocking the account deletion request."""
    if not any(asset_refs.values()):
        return
    app = current_app._get_current_object()
    try:
        ACCOUNT_CLEANUP_EXECUTOR.submit(_cleanup_in_background, app, asset_refs)
    except Exception:
        # The database deletion has already committed by the time this runs.
        # Never turn that successful deletion into a misleading 500 response.
        current_app.logger.exception("Could not queue background account asset cleanup")
