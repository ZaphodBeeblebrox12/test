"""
Diagnostic: why plans/trials do or don't render on /dashboard/ and /.

READ-ONLY — makes no changes. Run:
    python manage.py check_plan_visibility

Reports, per plan: flags, prices, geo prices, and every silent-absence cause
that matches the rendering rules in:
  - apps/accounts/views.py            (DashboardView — available_plans_geo)
  - apps/public_views/views.py        (LandingPageView._get_tiered_plans)
  - apps/subscriptions/services.py    (get_geo_price_for_trial / purchase_plan)
"""
from django.core.management.base import BaseCommand

from apps.subscriptions.models import Plan, PlanPrice, GeoPlanPrice


class Command(BaseCommand):
    help = "Diagnose plan/trial visibility on dashboard and landing page."

    def handle(self, *args, **options):
        plans = Plan.objects.all().order_by("tier", "display_order", "name")
        if not plans.exists():
            self.stdout.write(self.style.ERROR("No plans exist at all."))
            return

        problems = []
        for p in plans:
            prices = list(
                PlanPrice.objects.filter(plan=p, is_active=True)
                .values_list("interval", flat=True)
            )
            geo = list(
                GeoPlanPrice.objects.filter(plan=p, is_active=True)
                .values("country", "region", "price_cents", "currency")
            )
            self.stdout.write(
                f"\n{p.name!r}  tier={p.tier}  active={p.is_active}  "
                f"hidden={p.is_hidden}  trial={p.is_trial}"
                + (f" ({p.trial_duration_days}d)" if p.is_trial else "")
            )
            self.stdout.write(f"    prices(active PlanPrice): {prices or 'NONE'}")
            self.stdout.write(f"    geo prices(active GeoPlanPrice): {geo or 'NONE'}")

            if not p.is_active:
                problems.append(f"{p.name}: is_active=False -> invisible everywhere.")
                continue
            if p.is_hidden:
                # Expected for ticket-gated plans; not a defect.
                self.stdout.write(
                    self.style.WARNING(
                        f"    -> hidden: invisible on dashboard/landing/purchase "
                        f"(expected for ticket-gated plans; grant via support tickets)."
                    )
                )
                continue
            if p.is_trial:
                if not geo:
                    problems.append(
                        f"Trial {p.name}: NO GeoPlanPrice. Trials never display "
                        f"(dashboard or landing) without one; a plain PlanPrice "
                        f"does nothing for trials. Add a GeoPlanPrice in admin."
                    )
                else:
                    has_country_specific = any(g["country"] for g in geo)
                    if not has_country_specific:
                        problems.append(
                            f"Trial {p.name}: only country__isnull (global) geo "
                            f"price(s). Displays, but purchase_plan() REJECTS "
                            f"purchase without a country-specific row."
                        )
            else:
                if p.tier != "free" and not prices:
                    problems.append(
                        f"Paid plan {p.name} (tier {p.tier}): no active PlanPrice "
                        f"-> landing card SKIPPED (paid tiers need >=1 price), and "
                        f"any TRIAL in tier {p.tier} never shows either (the trial "
                        f"badge attaches to the first RENDERED card of its tier). "
                        f"Dashboard still lists it as 'Request access'."
                    )

        self.stdout.write("\n" + "=" * 70)
        if problems:
            self.stdout.write(self.style.ERROR("PROBLEMS FOUND:"))
            for prob in problems:
                self.stdout.write(self.style.ERROR("  - " + prob))
        else:
            self.stdout.write(self.style.SUCCESS(
                "No visibility problems detected for active, non-hidden plans."
            ))
        self.stdout.write(
            "Note: if /dashboard/ or / still looks wrong with no problem "
            "listed above, suspect stale code — restart runserver and confirm "
            "'git status' shows these files committed."
        )
