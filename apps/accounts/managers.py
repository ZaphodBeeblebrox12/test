"""
Custom user manager for community platform.
"""
from django.contrib.auth.models import BaseUserManager


class UserManager(BaseUserManager):
    """Custom user manager."""

    use_in_migrations = True

    def create_user(self, username, email=None, password=None, **extra_fields):
        """Create and save a regular user."""
        if not username:
            raise ValueError('The username must be set')

        email = self.normalize_email(email) if email else None
        user = self.model(
            username=username,
            email=email,
            **extra_fields
        )

        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()

        user.save(using=self._db)
        return user

    def create_superuser(self, username, email=None, password=None, **extra_fields):
        """Create and save a superuser."""
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)
        extra_fields.setdefault('role', 'admin')

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')

        return self.create_user(username, email, password, **extra_fields)
