## Project Structure

```
C:.
|   .env.example
|   .gitignore
|   ci_test_results.txt
|   docker-compose.yml
|   Dockerfile
|   file_tree.txt
|   Makefile
|   manage.py
|   ngrok.exe
|   pyproject.toml
|   pytest.ini
|   README.md
|   requirements.txt
|   test_telegram_config.py
|   
+---.github
|   \---workflows
|           ci.yml
|           
+---apps
|   |   __init__.py
|   |   
|   +---accounts
|   |   |   adapters.py
|   |   |   admin.py
|   |   |   admin_urls.py
|   |   |   apps.py
|   |   |   debug_views.py
|   |   |   discord_urls.py
|   |   |   discord_views.py
|   |   |   email_verification_middleware.py
|   |   |   email_verification_views.py
|   |   |   managers.py
|   |   |   models.py
|   |   |   profile_urls.py
|   |   |   serializers.py
|   |   |   signals.py
|   |   |   telegram_urls.py
|   |   |   telegram_views.py
|   |   |   urls.py
|   |   |   views.py
|   |   |   __init__.py
|   |   |   
|   |   +---config
|   |   |       urls.py
|   |   |       
|   |   +---management
|   |   |   |   __init__.py
|   |   |   |   
|   |   |   \---commands
|   |   |           create_socialapp.py
|   |   |           setup_google_auth.py
|   |   |           __init__.py
|   |   |           
|   |   +---migrations
|   |   |       0001_initial.py
|   |   |       0002_add_discord_fields.py
|   |   |       0002_alter_user_managers_alter_user_last_login.py
|   |   |       0003_add_discord_fields.py
|   |   |       0003_discordappconfig.py
|   |   |       0003_merge_20260316_2041.py
|   |   |       0004_merge_20260316_2048.py
|   |   |       0005_remove_user_discord_avatar_remove_user_discord_id_and_more.py
|   |   |       0006_merge_20260316_2243.py
|   |   |       0007_user_discord_avatar_user_discord_id_and_more.py
|   |   |       __init__.py
|   |   |       
|   |   +---services
|   |   |       discord_service.py
|   |   |       
|   |   \---templates
|   |       \---accounts
|   |               dashboard_discord_section.html
|   |               discord_error.html
|   |               profile_discord_section.html
|   |               telegram_login.html
|   |               
|   +---api
|   |   |   admin.py
|   |   |   authentication.py
|   |   |   middleware.py
|   |   |   models.py
|   |   |   urls.py
|   |   |   views.py
|   |   |   __init__.py
|   |   |   
|   |   \---migrations
|   |           0001_initial.py
|   |           0002_rename_api_apirequestlog_api_key_created_at_idx_api_apirequ_api_key_c39608_idx_and_more.py
|   |           __init__.py
|   |           
|   +---audit
|   |   |   admin.py
|   |   |   models.py
|   |   |   __init__.py
|   |   |   
|   |   \---migrations
|   |           0001_initial.py
|   |           0002_auditlog_audit_audit_action_766c6d_idx_and_more.py
|   |           0003_remove_auditlog_audit_auditlog_action_created_at_idx_and_more.py
|   |           __init__.py
|   |           
|   +---core
|   |   |   models.py
|   |   |   urls.py
|   |   |   views.py
|   |   |   __init__.py
|   |   |   
|   |   +---migrations
|   |   |       0001_initial.py
|   |   |       0002_delete_landingpage.py
|   |   |       __init__.py
|   |   |       
|   |   \---templatetags
|   |           custom_filters.py
|   |           __init__.py
|   |           
|   +---notifications
|   |   |   admin.py
|   |   |   context_processors.py
|   |   |   models.py
|   |   |   serializers.py
|   |   |   urls.py
|   |   |   views.py
|   |   |   __init__.py
|   |   |   
|   |   \---migrations
|   |           0001_initial.py
|   |           0002_rename_notificatio_user_id_7c0f9d_idx_notificatio_user_id_05b4bc_idx_and_more.py
|   |           __init__.py
|   |           
|   \---system_settings
|       |   admin.py
|       |   models.py
|       |   __init__.py
|       |   
|       \---migrations
|               0001_initial.py
|               __init__.py
|               
+---config
|   |   asgi.py
|   |   celery.py
|   |   urls.py
|   |   wsgi.py
|   |   __init__.py
|   |   
|   \---settings
|           base.py
|           development.py
|           production.py
|           __init__.py
|           
+---integrations
|   |   __init__.py
|   |   
|   \---telegram
|           auth.py
|           __init__.py
|           
+---services
|       __init__.py
|       
+---static
|   \---css
|           main.css
|           
+---tasks
|       __init__.py
|       
+---templates
|   |   base.html
|   |   index.html
|   |   logout.html
|   |   
|   +---account
|   |       email_confirm.html
|   |       email_confirmed.html
|   |       email_verification_sent.html
|   |       login.html
|   |       logout.html
|   |       signup.html
|   |       verification_required.html
|   |       
|   +---accounts
|   |       activity.html
|   |       banned.html
|   |       dashboard.html
|   |       dashboard_discord_section.html
|   |       notifications.html
|   |       profile.html
|   |       profile_discord_section.html
|   |       telegram_login.html
|   |       
|   \---notifications
|           list.html
|           
+---tests
|   |   conftest.py
|   |   __init__.py
|   |   
|   +---accounts
|   |       test_ban_system.py
|   |       test_staff_approval.py
|   |       test_telegram_auth.py
|   |       test_views.py
|   |       __init__.py
|   |       
|   +---api
|   |       test_authentication.py
|   |       __init__.py
|   |       
|   +---audit
|   |       test_audit_log.py
|   |       __init__.py
|   |       
|   +---core
|   |       __init__.py
|   |       
|   +---notifications
|   |       test_models.py
|   |       __init__.py
|   |       
|   \---system_settings
|           __init__.py
|           
\---utils
        __init__.py
        
```

# Django SocialApp.DoesNotExist Fix

## Problem
The Django server crashes with `allauth.socialaccount.models.SocialApp.DoesNotExist` because `templates/index.html` calls `{% provider_login_url "google" %}` but no SocialApp exists in the database.

## Solution
This patch provides:
1. **Defensive template logic** - Only shows Google button if SocialApp is configured
2. **Settings fixes** - Properly configures allauth and removes references to non-existent apps
3. **Management command** - Easy way to create SocialApp from environment variables

## Files Changed

### 1. config/settings/base.py
- Added `django.contrib.sites` to DJANGO_APPS (required for allauth)
- Added `allauth.socialaccount.providers.google` to THIRD_PARTY_APPS
- Commented out `apps.notifications` and `apps.api` from LOCAL_APPS (they don't exist yet)
- Commented out `apps.api.middleware.BanEnforcementMiddleware`
- Commented out `apps.api.authentication.APIKeyAuthentication`
- Added `SITE_ID = 1`, `LOGIN_REDIRECT_URL`, and `LOGOUT_REDIRECT_URL`

### 2. templates/index.html
- Wrapped Google button in defensive template logic:
  ```django
  {% get_providers as socialaccount_providers %}
  {% if socialaccount_providers %}
      {% for provider in socialaccount_providers %}
          {% if provider.id == 'google' %}
              <a href="{% provider_login_url 'google' %}">...</a>
          {% endif %}
      {% endfor %}
  {% endif %}
  ```

### 3. apps/accounts/management/commands/create_socialapp.py (NEW)
- Management command to create Google SocialApp from env vars
- Usage: `python manage.py create_socialapp`
- Requires: `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_SECRET`

## Installation

1. **Backup your current files:**
   ```bash
   cp config/settings/base.py config/settings/base.py.backup
   cp templates/index.html templates/index.html.backup
   ```

2. **Apply the patch:**
   ```bash
   # Option A: Using patch command
   patch -p1 < fix.patch

   # Option B: Manual copy
   cp fix_patch/config/settings/base.py config/settings/base.py
   cp fix_patch/templates/index.html templates/index.html
   cp -r fix_patch/apps/accounts/management apps/accounts/
   ```

3. **Install dependencies:**
   ```bash
   pip install django-allauth
   ```

4. **Run migrations:**
   ```bash
   python manage.py migrate
   python manage.py migrate sites  # Ensure sites framework is set up
   ```

5. **Create Site (if not exists):**
   ```bash
   python manage.py shell -c "from django.contrib.sites.models import Site; Site.objects.get_or_create(id=1, defaults={'domain': 'localhost:8000', 'name': 'localhost'})"
   ```

6. **Create SocialApp (optional - when ready for Google OAuth):**
   ```bash
   export GOOGLE_OAUTH_CLIENT_ID="your-client-id"
   export GOOGLE_OAUTH_SECRET="your-secret"
   python manage.py create_socialapp
   ```

## Verification

### Step 1: Django Check
```bash
python manage.py check
```
Expected output:
```
System check identified no issues (0 silenced).
```

### Step 2: Migrations
```bash
python manage.py migrate
```
Expected output:
```
Operations to perform:
  Apply all migrations: admin, auth, contenttypes, sessions, sites, allauth, accounts, audit, core, system_settings
Running migrations:
  Applying sites.0001_initial... OK
  Applying allauth.account... OK
  Applying allauth.socialaccount... OK
  Applying allauth.socialaccount.providers.google... OK
```

### Step 3: Run Server
```bash
python manage.py runserver
```

### Step 4: Test Homepage
- Visit http://127.0.0.1:8000/
- Page should load without errors
- Google button should NOT appear (since no SocialApp configured)
- Telegram button should work

### Step 5: Test with SocialApp
After running `python manage.py create_socialapp`:
- Refresh http://127.0.0.1:8000/
- Google button should now appear
- Clicking Google button should redirect to Google OAuth

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GOOGLE_OAUTH_CLIENT_ID` | No | Google OAuth Client ID |
| `GOOGLE_OAUTH_SECRET` | No | Google OAuth Client Secret |
| `TELEGRAM_BOT_TOKEN` | No | Telegram Bot Token |
| `TELEGRAM_BOT_USERNAME` | No | Telegram Bot Username |

## Rollback

If you need to rollback:
```bash
cp config/settings/base.py.backup config/settings/base.py
cp templates/index.html.backup templates/index.html
rm -rf apps/accounts/management
```

## Notes

- Phase 1 functionality (Telegram auth, ban system, audit logging) remains unchanged
- Google OAuth is now opt-in via the `create_socialapp` command
- The defensive template logic prevents crashes when SocialApp is missing
- When you're ready to add `apps.api` and `apps.notifications`, uncomment the relevant lines in settings
