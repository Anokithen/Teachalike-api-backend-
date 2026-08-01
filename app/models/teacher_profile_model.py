"""Private teacher application and approval metadata."""

from app.extensions import db
from app.utils import utc_isoformat, utc_now


TEACHER_TYPE_SCHOOL = "school"
TEACHER_TYPE_PRIVATE_TUITION = "private_tuition"
VALID_TEACHER_TYPES = (TEACHER_TYPE_SCHOOL, TEACHER_TYPE_PRIVATE_TUITION)

APPROVAL_PENDING = "pending"
APPROVAL_APPROVED = "approved"
APPROVAL_REJECTED = "rejected"
VALID_APPROVAL_STATUSES = (
    APPROVAL_PENDING,
    APPROVAL_APPROVED,
    APPROVAL_REJECTED,
)


class TeacherProfile(db.Model):
    __tablename__ = "teacher_profiles"
    __table_args__ = (
        db.Index("ix_teacher_profiles_approval_status", "approval_status"),
        db.Index("ix_teacher_profiles_reviewed_by_id", "reviewed_by_id"),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    account_id = db.Column(
        db.Integer,
        db.ForeignKey("parents.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    phone_number = db.Column(db.String(40), nullable=True)
    address = db.Column(db.String(500), nullable=True)
    teacher_type = db.Column(db.String(30), nullable=True)
    school_name = db.Column(db.String(200), nullable=True)
    tuition_name = db.Column(db.String(200), nullable=True)
    approval_status = db.Column(
        db.String(20), nullable=False, default=APPROVAL_PENDING
    )
    reviewed_by_id = db.Column(
        db.Integer,
        db.ForeignKey("parents.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at = db.Column(db.DateTime, nullable=True)
    rejection_reason = db.Column(db.String(1000), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=utc_now, onupdate=utc_now
    )

    account = db.relationship(
        "Parent",
        foreign_keys=[account_id],
        back_populates="teacher_profile",
    )
    reviewed_by = db.relationship(
        "Parent",
        foreign_keys=[reviewed_by_id],
        back_populates="reviewed_teacher_profiles",
    )

    def to_private_dict(self):
        return {
            "phone_number": self.phone_number,
            "address": self.address,
            "teacher_type": self.teacher_type,
            "school_name": self.school_name,
            "tuition_name": self.tuition_name,
            "approval_status": self.approval_status,
            "reviewed_by_id": self.reviewed_by_id,
            "reviewed_at": utc_isoformat(self.reviewed_at),
            "rejection_reason": self.rejection_reason,
            "created_at": utc_isoformat(self.created_at),
            "updated_at": utc_isoformat(self.updated_at),
        }

