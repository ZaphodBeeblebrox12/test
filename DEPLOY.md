# DEPLOYING TO RENDER (free plan)

## What this pack contains
| File | Action |
|---|---|
| `config/settings/production.py` | NEW |
| `config/wsgi.py` | REPLACE (defaults to production settings) |
| `build.sh` | NEW — repo root |
| `render.yaml` | NEW — repo root (Render Blueprint) |
| `requirements.txt` | REPLACE (adds gunicorn + psycopg3) |

## Why Postgres is mandatory
Render free web services have an EPHEMERAL filesystem: anything written
to disk (including db.sqlite3) disappears on every deploy/restart. SQLite
is therefore not an option. base.py already reads DATABASE_URL via
django-environ, and render.yaml provisions a free PostgreSQL and injects
DATABASE_URL automatically.

## Steps
1. Copy the 5 files into the repo (wsgi.py + requirements.txt replace).
2. Commit + push to main.
3. render.com -> Dashboard -> New + -> Blueprint -> connect the repo.
   Render reads render.yaml, creates the Postgres DB and the web service,
   and deploys. First deploy takes ~3-5 min (pip + collectstatic + migrate).
4. After deploy: Dashboard -> tradeadmin -> Environment -> fill in the
   `sync: false` secrets (Telegram tokens, Stripe/Razorpay keys, SMTP).
5. Create the admin account:
       Render shell -> python manage.py createsuperuser
   (or: python manage.py shell -c "from django.contrib.auth import get_user_model; get_user_model().objects.create_superuser('admin','a@b.c','PASS')")
6. Recreate your plans/products/Prices in the admin — the production DB
   starts EMPTY (your dev SQLite data does not travel).

## MUST-DO before opening registration
ACCOUNT_EMAIL_VERIFICATION = "mandatory" in base.py. Without working SMTP,
verification emails go nowhere and nobody can sign up. Set EMAIL_* env vars
(Gmail: enable 2FA -> create an App Password; or SendGrid free tier, 100
emails/day). Test with the "forgot password" flow yourself first.
No SMTP and want to launch anyway? Temporarily set env:
    EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
(verification links appear in Render logs — fine for a private beta.)

## Free-tier realities (plan around these)
- SPIN-DOWN: after ~15 min idle the service sleeps; first request takes
  ~30-60s. Upgrade or live with it.
- 750 INSTANCE HOURS/month total. ONE always-on service fits; TWO
  always-on services (e.g. web + a background jobs worker) do NOT.
  The jobs worker (python manage.py runjobs --loop) that runs reconcile/
  sweeps is a SEPARATE process — on free you either skip it (web requests
  still trigger reconcile for that user) or run it briefly when needed.
- FREE POSTGRES EXPIRES: Render's free database is time-limited (they
  email before). Back up with pg_dump / django dumpdata before expiry
  and restore into a new free DB. (Verify current terms on render.com.)
- MEDIA FILES (avatars, ticket attachments) are on the ephemeral disk and
  are LOST on redeploy. Acceptable for launch; move to Cloudinary/S3 later.
- Geo pricing: CF-IPCountry headers won't exist on Render; set
  MAXMIND_ENABLED=True (db already committed in data/) and test geo prices
  with the DEBUG-only ?test_country=IN param — but DEBUG is False in prod,
  so instead add real GeoPlanPrice rows and verify from an Indian IP/VPN.
- Stripe/Razorpay webhooks: point them at https://<app>.onrender.com/
  (check apps/payments/urls.py for the exact webhook paths) — localhost
  webhook secrets from dev are invalid there.
- Telegram Login Widget: the widget verifies against the bot's configured
  domain — your Render URL must be registered with @BotFather (/setdomain).

## Post-deploy smoke test
/ -> landing renders (plans from empty DB -> create them first)
/admin -> Jazzmin login -> Plans/Products visible
/accounts/signup/ -> email arrives -> confirm -> /dashboard/ renders
python manage.py check --deploy   (from Render shell; fix any warnings)
