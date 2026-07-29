"""Cloudinary asset metadata model."""

from app.extensions import db
from app.utils import utc_isoformat, utc_now

USER_PROFILE_IMAGE = "USER_PROFILE_IMAGE"
CHILD_PROFILE_IMAGE = "CHILD_PROFILE_IMAGE"
VOICE_PROFILE = "VOICE_PROFILE"
GENERATED_BOOK_AUDIO = "GENERATED_BOOK_AUDIO"
BOOK_VIDEO = "BOOK_VIDEO"

ASSET_CATEGORIES = (
    USER_PROFILE_IMAGE,
    CHILD_PROFILE_IMAGE,
    VOICE_PROFILE,
    GENERATED_BOOK_AUDIO,
    BOOK_VIDEO,
)

STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_DELETED = "deleted"
STATUS_CLEANUP_FAILED = "cleanup_failed"


class Asset(db.Model):
    """A durable record for an externally stored Cloudinary asset."""

    __tablename__ = "assets"
    __table_args__ = (
        db.Index("ix_assets_owner_user_id", "owner_user_id"),
        db.Index("ix_assets_book_id", "book_id"),
        db.Index("ix_assets_voice_profile_id", "voice_profile_id"),
        db.UniqueConstraint("active_slot", name="uq_assets_active_slot"),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    owner_user_id = db.Column(
        db.Integer, db.ForeignKey("parents.id", ondelete="CASCADE"), nullable=False
    )
    child_id = db.Column(
        db.Integer, db.ForeignKey("children.id", ondelete="SET NULL"), nullable=True
    )
    book_id = db.Column(
        db.Integer, db.ForeignKey("books.id", ondelete="SET NULL"), nullable=True
    )
    admin_id = db.Column(
        db.Integer, db.ForeignKey("parents.id", ondelete="SET NULL"), nullable=True
    )
    voice_profile_id = db.Column(
        db.Integer, db.ForeignKey("voice_profiles.id", ondelete="SET NULL"), nullable=True
    )
    generation_id = db.Column(
        db.Integer, db.ForeignKey("book_narrations.id", ondelete="SET NULL"), nullable=True
    )
    asset_category = db.Column(db.String(40), nullable=False)
    # Non-NULL only for singleton assets; MySQL permits multiple NULLs while
    # enforcing exactly one active row for each deterministic slot.
    active_slot = db.Column(db.String(255), nullable=True)
    # Historical rows for deterministic overwrites may identify different
    # versions of the same logical Cloudinary asset.
    cloudinary_asset_id = db.Column(db.String(255), nullable=False)
    # Replacements retain history and may reuse a deterministic public ID.
    cloudinary_public_id = db.Column(db.String(500), nullable=False)
    cloudinary_secure_url = db.Column(db.String(1000), nullable=False)
    cloudinary_resource_type = db.Column(db.String(20), nullable=False)
    cloudinary_delivery_type = db.Column(db.String(30), nullable=False, default="upload")
    cloudinary_format = db.Column(db.String(30), nullable=True)
    cloudinary_asset_folder = db.Column(db.String(500), nullable=False)
    original_filename = db.Column(db.String(255), nullable=True)
    file_size_bytes = db.Column(db.BigInteger, nullable=True)
    width = db.Column(db.Integer, nullable=True)
    height = db.Column(db.Integer, nullable=True)
    duration_seconds = db.Column(db.Float, nullable=True)
    status = db.Column(db.String(20), nullable=False, default=STATUS_COMPLETED)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now)
    deleted_at = db.Column(db.DateTime, nullable=True)

    @classmethod
    def from_cloudinary_metadata(
        cls,
        metadata,
        *,
        category,
        owner_user_id,
        active_slot=None,
        status=STATUS_COMPLETED,
        **relations,
    ):
        """Build an asset row from normalized storage-service metadata."""
        return cls(
            owner_user_id=owner_user_id,
            asset_category=category,
            active_slot=active_slot,
            cloudinary_asset_id=metadata["asset_id"],
            cloudinary_public_id=metadata["public_id"],
            cloudinary_secure_url=metadata["secure_url"],
            cloudinary_resource_type=metadata["resource_type"],
            cloudinary_delivery_type=metadata.get("delivery_type") or "upload",
            cloudinary_format=metadata.get("format"),
            cloudinary_asset_folder=metadata["asset_folder"],
            original_filename=metadata.get("original_filename"),
            file_size_bytes=metadata.get("bytes"),
            width=metadata.get("width"),
            height=metadata.get("height"),
            duration_seconds=metadata.get("duration"),
            status=status,
            **relations,
        )

    def to_dict(self) -> dict:
        """Return the safe, client-facing representation."""
        return {
            "id": self.id,
            "owner_user_id": self.owner_user_id,
            "child_id": self.child_id,
            "book_id": self.book_id,
            "admin_id": self.admin_id,
            "voice_profile_id": self.voice_profile_id,
            "generation_id": self.generation_id,
            "asset_category": self.asset_category,
            "url": self.cloudinary_secure_url,
            "resource_type": self.cloudinary_resource_type,
            "format": self.cloudinary_format,
            "original_filename": self.original_filename,
            "file_size_bytes": self.file_size_bytes,
            "width": self.width,
            "height": self.height,
            "duration_seconds": self.duration_seconds,
            "status": self.status,
            "created_at": utc_isoformat(self.created_at),
            "updated_at": utc_isoformat(self.updated_at),
        }
