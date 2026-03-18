Folder PATH listing
Volume serial number is 421D-F8AC
C:.
|   .env.example
|   .gitignore
|   ci_test_results.txt
|   db.sqlite3
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
|   |   |   views.py.patch
|   |   |   __init__.py
|   |   |   
|   |   +---config
|   |   |       urls.py
|   |   |       
|   |   +---management
|   |   |   |   __init__.py
|   |   |   |   
|   |   |   +---commands
|   |   |   |       create_socialapp.py
|   |   |   |       setup_google_auth.py
|   |   |   |       __init__.py
|   |   |   |       
|   |   |   \---__pycache__
|   |   |           __init__.cpython-312.pyc
|   |   |           
|   |   +---migrations
|   |   |   |   0001_initial.py
|   |   |   |   0002_add_discord_fields.py
|   |   |   |   0002_alter_user_managers_alter_user_last_login.py
|   |   |   |   0003_add_discord_fields.py
|   |   |   |   0003_discordappconfig.py
|   |   |   |   0003_merge_20260316_2041.py
|   |   |   |   0004_merge_20260316_2048.py
|   |   |   |   0005_remove_user_discord_avatar_remove_user_discord_id_and_more.py
|   |   |   |   0006_merge_20260316_2243.py
|   |   |   |   0007_user_discord_avatar_user_discord_id_and_more.py
|   |   |   |   __init__.py
|   |   |   |   
|   |   |   \---__pycache__
|   |   |           0001_initial.cpython-312.pyc
|   |   |           0002_add_discord_fields.cpython-312.pyc
|   |   |           0002_alter_user_managers_alter_user_last_login.cpython-312.pyc
|   |   |           0003_add_discord_fields.cpython-312.pyc
|   |   |           0003_discordappconfig.cpython-312.pyc
|   |   |           0003_merge_20260316_2041.cpython-312.pyc
|   |   |           0004_merge_20260316_2048.cpython-312.pyc
|   |   |           0005_remove_user_discord_avatar_remove_user_discord_id_and_more.cpython-312.pyc
|   |   |           0006_merge_20260316_2243.cpython-312.pyc
|   |   |           0007_user_discord_avatar_user_discord_id_and_more.cpython-312.pyc
|   |   |           __init__.cpython-312.pyc
|   |   |           
|   |   +---services
|   |   |   |   discord_service.py
|   |   |   |   
|   |   |   \---__pycache__
|   |   |           discord_service.cpython-312.pyc
|   |   |           
|   |   +---templates
|   |   |   \---accounts
|   |   |           dashboard_discord_section.html
|   |   |           discord_error.html
|   |   |           profile_discord_section.html
|   |   |           telegram_login.html
|   |   |           
|   |   \---__pycache__
|   |           admin.cpython-312.pyc
|   |           admin_urls.cpython-312.pyc
|   |           apps.cpython-312.pyc
|   |           debug_views.cpython-312.pyc
|   |           discord_urls.cpython-312.pyc
|   |           discord_views.cpython-312.pyc
|   |           email_verification_middleware.cpython-312.pyc
|   |           managers.cpython-312.pyc
|   |           models.cpython-312.pyc
|   |           profile_urls.cpython-312.pyc
|   |           serializers.cpython-312.pyc
|   |           signals.cpython-312.pyc
|   |           telegram_urls.cpython-312.pyc
|   |           telegram_views.cpython-312.pyc
|   |           urls.cpython-312.pyc
|   |           views.cpython-312.pyc
|   |           __init__.cpython-312.pyc
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
|   |   +---migrations
|   |   |   |   0001_initial.py
|   |   |   |   0002_rename_api_apirequestlog_api_key_created_at_idx_api_apirequ_api_key_c39608_idx_and_more.py
|   |   |   |   __init__.py
|   |   |   |   
|   |   |   \---__pycache__
|   |   |           0001_initial.cpython-312.pyc
|   |   |           0002_rename_api_apirequestlog_api_key_created_at_idx_api_apirequ_api_key_c39608_idx_and_more.cpython-312.pyc
|   |   |           __init__.cpython-312.pyc
|   |   |           
|   |   \---__pycache__
|   |           admin.cpython-312.pyc
|   |           models.cpython-312.pyc
|   |           urls.cpython-312.pyc
|   |           views.cpython-312.pyc
|   |           __init__.cpython-312.pyc
|   |           
|   +---audit
|   |   |   admin.py
|   |   |   models.py
|   |   |   __init__.py
|   |   |   
|   |   +---migrations
|   |   |   |   0001_initial.py
|   |   |   |   0002_auditlog_audit_audit_action_766c6d_idx_and_more.py
|   |   |   |   0003_remove_auditlog_audit_auditlog_action_created_at_idx_and_more.py
|   |   |   |   __init__.py
|   |   |   |   
|   |   |   \---__pycache__
|   |   |           0001_initial.cpython-312.pyc
|   |   |           0002_auditlog_audit_audit_action_766c6d_idx_and_more.cpython-312.pyc
|   |   |           0003_remove_auditlog_audit_auditlog_action_created_at_idx_and_more.cpython-312.pyc
|   |   |           __init__.cpython-312.pyc
|   |   |           
|   |   \---__pycache__
|   |           admin.cpython-312.pyc
|   |           models.cpython-312.pyc
|   |           __init__.cpython-312.pyc
|   |           
|   +---core
|   |   |   models.py
|   |   |   urls.py
|   |   |   views.py
|   |   |   __init__.py
|   |   |   
|   |   +---migrations
|   |   |   |   0001_initial.py
|   |   |   |   0002_delete_landingpage.py
|   |   |   |   __init__.py
|   |   |   |   
|   |   |   \---__pycache__
|   |   |           0001_initial.cpython-312.pyc
|   |   |           0002_delete_landingpage.cpython-312.pyc
|   |   |           __init__.cpython-312.pyc
|   |   |           
|   |   +---templatetags
|   |   |   |   custom_filters.py
|   |   |   |   __init__.py
|   |   |   |   
|   |   |   \---__pycache__
|   |   |           custom_filters.cpython-312.pyc
|   |   |           __init__.cpython-312.pyc
|   |   |           
|   |   \---__pycache__
|   |           models.cpython-312.pyc
|   |           urls.cpython-312.pyc
|   |           views.cpython-312.pyc
|   |           __init__.cpython-312.pyc
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
|   |   +---migrations
|   |   |   |   0001_initial.py
|   |   |   |   0002_rename_notificatio_user_id_7c0f9d_idx_notificatio_user_id_05b4bc_idx_and_more.py
|   |   |   |   __init__.py
|   |   |   |   
|   |   |   \---__pycache__
|   |   |           0001_initial.cpython-312.pyc
|   |   |           0002_rename_notificatio_user_id_7c0f9d_idx_notificatio_user_id_05b4bc_idx_and_more.cpython-312.pyc
|   |   |           __init__.cpython-312.pyc
|   |   |           
|   |   \---__pycache__
|   |           admin.cpython-312.pyc
|   |           models.cpython-312.pyc
|   |           serializers.cpython-312.pyc
|   |           urls.cpython-312.pyc
|   |           views.cpython-312.pyc
|   |           __init__.cpython-312.pyc
|   |           
|   +---subscriptions
|   |   |   admin.py
|   |   |   models.py
|   |   |   serializers.py
|   |   |   urls.py
|   |   |   views.py
|   |   |   __init__.py
|   |   |   
|   |   +---migrations
|   |   |   |   0001_initial.py
|   |   |   |   __init__.py
|   |   |   |   
|   |   |   \---__pycache__
|   |   |           0001_initial.cpython-312.pyc
|   |   |           __init__.cpython-312.pyc
|   |   |           
|   |   \---__pycache__
|   |           admin.cpython-312.pyc
|   |           models.cpython-312.pyc
|   |           __init__.cpython-312.pyc
|   |           
|   +---system_settings
|   |   |   admin.py
|   |   |   models.py
|   |   |   __init__.py
|   |   |   
|   |   +---migrations
|   |   |   |   0001_initial.py
|   |   |   |   __init__.py
|   |   |   |   
|   |   |   \---__pycache__
|   |   |           0001_initial.cpython-312.pyc
|   |   |           __init__.cpython-312.pyc
|   |   |           
|   |   \---__pycache__
|   |           admin.cpython-312.pyc
|   |           models.cpython-312.pyc
|   |           __init__.cpython-312.pyc
|   |           
|   \---__pycache__
|           __init__.cpython-312.pyc
|           
+---config
|   |   asgi.py
|   |   celery.py
|   |   urls.py
|   |   urls.py.new
|   |   urls.py.patch
|   |   wsgi.py
|   |   __init__.py
|   |   
|   +---settings
|   |   |   base.py
|   |   |   development.py
|   |   |   production.py
|   |   |   __init__.py
|   |   |   
|   |   \---__pycache__
|   |           base.cpython-312.pyc
|   |           development.cpython-312.pyc
|   |           __init__.cpython-312.pyc
|   |           
|   \---__pycache__
|           urls.cpython-312.pyc
|           wsgi.cpython-312.pyc
|           __init__.cpython-312.pyc
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
|   |       dashboard.html.new
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
        
