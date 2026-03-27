
# Add this field to your User model in apps/accounts/models.py

class User(AbstractUser):
    # ... existing fields ...

    # NEW: Custom display name for gifts/emails
    nickname = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name='Display Name',
        help_text='Custom display name shown in gift emails. Falls back to first name if empty.',
    )

    # ... rest of your model ...

    def get_display_name(self) -> str:
        """
        Return the best display name for this user.
        Priority: nickname > first_name > username
        """
        if self.nickname:
            return self.nickname
        if self.first_name:
            return self.first_name
        return self.username
