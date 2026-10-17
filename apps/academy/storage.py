"""Private file storage for instructional images.

Files are not placed in MEDIA_ROOT, which may be served directly in development.
Set ACADEMY_PRIVATE_MEDIA_ROOT to a durable mounted path in production.
"""
from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible


@deconstructible
class PrivateCourseAssetStorage(FileSystemStorage):
    def __init__(self):
        location = getattr(
            settings,
            "ACADEMY_PRIVATE_MEDIA_ROOT",
            Path(settings.BASE_DIR) / "private_course_media",
        )
        super().__init__(location=str(location), base_url=settings.MEDIA_URL)


course_asset_storage = PrivateCourseAssetStorage()
