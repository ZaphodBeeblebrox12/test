"""Narrow authorization policy for shared instructional content."""
def is_course_content_admin(user):
    """Only superusers or users explicitly assigned the ADMIN role may edit shared content.

    Do not substitute is_staff: ordinary operational staff must remain read-only.
    """
    if not getattr(user, "is_authenticated", False):
        return False
    return bool(getattr(user, "is_superuser", False) or getattr(user, "role", None) == "admin")
