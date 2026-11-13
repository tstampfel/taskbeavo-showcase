"""Staff notes in account deletion (plan: "GDPR flows"; user decision 2026-11-04).

Notes are named by booking, not by person, so deleting the `User` row doesn't reach
them. TODO(BEAV-153): the account deletion job calls `redact_notes_about(user.pk)` for
the deleted person. Notes under a legal hold stay until the matter is resolved; the
deletion then completes when `staff.sweep_notes` deletes them.
"""

from staff.retention import redact_notes_about

__all__ = ["redact_notes_about"]
