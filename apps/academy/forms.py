"""Server-side form validation for administrator course and media editing."""
from contextlib import suppress
from urllib.parse import urlsplit

from django import forms
from django.core.exceptions import ValidationError
from PIL import Image, UnidentifiedImageError

from .content_rendering import validate_markdown_source
from .models import CourseImportAsset, CourseMediaAsset, Lesson

_ALLOWED_IMAGE_FORMATS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
_MAX_IMAGE_BYTES = 10 * 1024 * 1024


def validate_upload_image(upload):
    if upload is None:
        return
    if upload.size > _MAX_IMAGE_BYTES:
        raise ValidationError("Images must be 10 MB or smaller.")
    try:
        upload.seek(0)
        image = Image.open(upload)
        image.verify()
        upload.seek(0)
        image = Image.open(upload)
        image_format = image.format
        if image_format not in _ALLOWED_IMAGE_FORMATS:
            raise ValidationError("Use a valid JPEG, PNG or WebP image. SVG uploads are not accepted.")
        image.load()
        # Use a server-selected suffix rather than the browser-supplied filename.
        upload.name = "course-image" + _ALLOWED_IMAGE_FORMATS[image_format]
    except ValidationError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValidationError("The uploaded file is not a valid supported image.") from exc
    finally:
        with suppress(AttributeError, OSError):
            upload.seek(0)


class LessonAdminForm(forms.ModelForm):
    class Meta:
        model = Lesson
        fields = "__all__"

    def clean_content_markdown(self):
        return validate_markdown_source(self.cleaned_data.get("content_markdown", ""))


class LessonFigureAdminForm(forms.Form):
    """Add one independently replaceable figure to a lesson."""

    PLACEMENT_CHOICES = (
        ("idea", "The idea"),
        ("examples", "Chart examples"),
        ("practice", "Test your reading"),
    )

    alt_text = forms.CharField(
        label="Image description",
        max_length=500,
        help_text="Describe what a learner should notice in the figure.",
    )
    original_url = forms.URLField(
        label="Temporary image URL",
        required=False,
        help_text="An existing HTTP(S) image URL can render now and be replaced by an upload later.",
    )
    file = forms.ImageField(
        label="Upload your own image (optional)",
        required=False,
        help_text="JPEG, PNG or WebP, maximum 10 MB. An upload overrides the URL while preserving it as fallback.",
    )
    placement = forms.ChoiceField(
        label="Learning Room tab",
        choices=PLACEMENT_CHOICES,
        initial="examples",
    )

    def clean_alt_text(self):
        value = " ".join(self.cleaned_data["alt_text"].split())
        if "[" in value or "]" in value:
            raise ValidationError("Please describe the image without square brackets.")
        return value

    def clean_original_url(self):
        value = self.cleaned_data.get("original_url", "").strip()
        if value and urlsplit(value).scheme.lower() not in {"http", "https"}:
            raise ValidationError("Image URLs must use HTTP or HTTPS.")
        return value

    def clean_file(self):
        upload = self.cleaned_data.get("file")
        validate_upload_image(upload)
        return upload

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("original_url") and not cleaned.get("file"):
            raise ValidationError("Enter an image URL or upload an image.")
        return cleaned


class CourseImportAssetAdminForm(forms.ModelForm):
    class Meta:
        model = CourseImportAsset
        fields = "__all__"

    def clean_file(self):
        upload = self.cleaned_data.get("file")
        validate_upload_image(upload)
        return upload


class CourseMediaAssetAdminForm(forms.ModelForm):
    class Meta:
        model = CourseMediaAsset
        fields = "__all__"

    def clean_file(self):
        upload = self.cleaned_data.get("file")
        validate_upload_image(upload)
        return upload
