"""Forms for support tickets."""
from django import forms

from .models import AffiliateLink, MAX_ATTACHMENTS_PER_TICKET, SupportTicket


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """FileField that accepts multiple uploads (Django 3.2+ pattern)."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_clean = super().clean
        if isinstance(data, (list, tuple)):
            result = [single_clean(d, initial) for d in data]
            if len(result) > MAX_ATTACHMENTS_PER_TICKET:
                raise forms.ValidationError(
                    f"Too many files: maximum {MAX_ATTACHMENTS_PER_TICKET} screenshots.")
            return result
        return single_clean(data, initial)


class SupportTicketForm(forms.ModelForm):
    affiliate_link = forms.ModelChoiceField(
        queryset=AffiliateLink.objects.filter(is_active=True),
        required=False,
        label="Referral link used",
        help_text="Which of our referral links you signed up through.",
    )
    attachments = MultipleFileField(
        required=False,
        label="Screenshots",
        help_text=f"Up to {MAX_ATTACHMENTS_PER_TICKET} images (PNG/JPG/GIF/WEBP, max 5 MB each). "
                  "Required for access requests; optional for other issues.")

    class Meta:
        model = SupportTicket
        fields = ["category", "affiliate_link", "message"]
        widgets = {
            "message": forms.Textarea(attrs={
                "rows": 4,
                "placeholder": "Describe your request or issue...",
            }),
        }

    def clean(self):
        cleaned = super().clean()
        category = cleaned.get("category")
        if category == SupportTicket.Category.ACCESS:
            if not cleaned.get("affiliate_link"):
                self.add_error("affiliate_link",
                               "Select the referral link you used.")
            if not cleaned.get("attachments"):
                self.add_error("attachments",
                               "Upload at least one screenshot for access requests.")
        return cleaned
