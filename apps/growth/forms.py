"""
Growth forms for gift creation and management.
"""
from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


class GiftCreateForm(forms.Form):
    """
    Form for creating a new gift subscription.

    This form is used in admin or API endpoints to create gifts.
    The actual creation is handled by GiftService.

    CRITICAL: Plan queryset must be passed from view via __init__
    to maintain boundary (growth should not import subscriptions.models).
    """

    recipient_email = forms.EmailField(
        label=_("Recipient Email"),
        help_text=_("The email address of the person receiving this gift")
    )

    # Plan field - queryset set via __init__ from view layer
    plan = forms.ModelChoiceField(
        queryset=None,  # Set in __init__ from view
        label=_("Plan"),
        help_text=_("The subscription plan to gift")
    )

    duration_days = forms.IntegerField(
        min_value=1,
        max_value=365,
        initial=30,
        label=_("Duration (days)"),
        help_text=_("How many days the gift subscription lasts")
    )

    message = forms.CharField(
        widget=forms.Textarea,
        required=False,
        label=_("Personal Message"),
        help_text=_("Optional message for the recipient"),
        max_length=1000
    )

    def __init__(self, from_user=None, plan_queryset=None, *args, **kwargs):
        """
        Initialize form with optional plan queryset.

        Args:
            from_user: User giving the gift (for self-gift check)
            plan_queryset: QuerySet of Plan objects (passed from view to maintain boundary)
        """
        super().__init__(*args, **kwargs)
        self.from_user = from_user

        # Set plan queryset if provided (maintains boundary - view passes this)
        if plan_queryset is not None:
            self.fields['plan'].queryset = plan_queryset

    def clean_recipient_email(self):
        """Validate recipient email."""
        email = self.cleaned_data['recipient_email'].lower().strip()

        # Check for self-gifting
        if self.from_user and self.from_user.email:
            if email == self.from_user.email.lower():
                raise ValidationError(
                    _("You cannot gift a subscription to yourself.")
                )

        return email


class GiftResendEmailForm(forms.Form):
    """Form for resending gift email."""

    confirm = forms.BooleanField(
        required=True,
        label=_("Confirm Resend"),
        help_text=_("I confirm I want to resend this gift email")
    )


class LegacyGiftClaimForm(forms.Form):
    """
    Form for claiming a legacy gift by code.

    This is for the old gift_code-based flow (e.g., "ABC123XY").
    """

    gift_code = forms.CharField(
        label=_("Gift Code"),
        max_length=20,
        help_text=_("Enter the gift code you received (e.g., ABC123XY)"),
        widget=forms.TextInput(attrs={
            'placeholder': 'ABC123XY',
            'class': 'form-control',
            'style': 'text-transform: uppercase;'
        })
    )

    def clean_gift_code(self):
        """Normalize the gift code."""
        code = self.cleaned_data['gift_code']
        return code.upper().strip()



class AdminGiftSendForm(forms.Form):
    """
    Simple form for admin to send gifts.

    Clean, minimal UX for non-technical staff.
    """

    recipient_email = forms.EmailField(
        label="Recipient Email",
        widget=forms.EmailInput(attrs={
            'class': 'vTextField',
            'placeholder': 'friend@example.com',
        }),
        help_text="The email address of the person receiving this gift"
    )

    plan = forms.ModelChoiceField(
        queryset=None,  # Set in view
        label="Plan",
        widget=forms.Select(attrs={'class': 'vSelect'}),
        help_text="Select the subscription plan to gift"
    )

    duration_days = forms.ChoiceField(
        label="Duration",
        choices=[
            (7, "1 week"),
            (30, "1 month"),
            (90, "3 months"),
            (365, "1 year"),
        ],
        initial=30,
        widget=forms.Select(attrs={'class': 'vSelect'}),
        help_text="How long the gift subscription lasts"
    )

    message = forms.CharField(
        label="Personal Message (Optional)",
        required=False,
        widget=forms.Textarea(attrs={
            'class': 'vLargeTextField',
            'rows': 3,
            'placeholder': 'Enjoy your gift! 🎁',
        }),
        help_text="A personal message for the recipient"
    )
