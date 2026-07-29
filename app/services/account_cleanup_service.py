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
