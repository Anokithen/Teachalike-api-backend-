"""Compatibility imports for the renamed teacher application entity."""

from app.models.teacher_application_model import (
    APPROVAL_APPROVED,
    APPROVAL_PENDING,
    APPROVAL_REJECTED,
    TEACHER_TYPE_PRIVATE_TUITION,
    TEACHER_TYPE_SCHOOL,
    VALID_APPROVAL_STATUSES,
    VALID_TEACHER_TYPES,
    TeacherApplication,
)

# Older integrations may still import TeacherProfile. Keep the Python alias
# while all persistence now targets the teacher_applications table.
TeacherProfile = TeacherApplication

__all__ = [
    "APPROVAL_APPROVED",
    "APPROVAL_PENDING",
    "APPROVAL_REJECTED",
    "TEACHER_TYPE_PRIVATE_TUITION",
    "TEACHER_TYPE_SCHOOL",
    "VALID_APPROVAL_STATUSES",
    "VALID_TEACHER_TYPES",
    "TeacherApplication",
    "TeacherProfile",
]
