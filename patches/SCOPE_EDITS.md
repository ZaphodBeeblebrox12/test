# SCOPE EDITS — three tiny, exact search/replace edits for product scoping

Apply with care: each "FIND" block must match your file EXACTLY (it matches
the branch verbatim). If a FIND does not match, STOP and diff the file —
do not force it.

## 1) apps/subscriptions/services.py — purchase_plan()

FIND:
    # Deactivate any existing active subscriptions
    Subscription.objects.filter(user=user, is_active=True).update(
        is_active=False, status=Subscription.Status.CANCELED, canceled_at=timezone.now()
    )

REPLACE WITH:
    # Deactivate existing active subscriptions IN THE SAME PRODUCT only;
    # subscriptions in other products are independent entitlements.
    # product=None keeps the legacy GLOBAL behavior.
    same_product = Subscription.objects.filter(user=user, is_active=True)
    if plan.product_id is not None:
        same_product = same_product.filter(product_id=plan.product_id)
    same_product.update(
        is_active=False, status=Subscription.Status.CANCELED, canceled_at=timezone.now()
    )

## 2) apps/payments/services.py — compute_upgrade_quote()

FIND:
    subscription = (Subscription.objects
                    .filter(user=user, status=Subscription.Status.ACTIVE,
                            is_active=True)
                    .select_related("plan", "plan_price").first())

REPLACE WITH:
    subscription = (Subscription.objects
                    .filter(user=user, status=Subscription.Status.ACTIVE,
                            is_active=True)
                    .select_related("plan", "plan_price"))
    if target_plan.product_id is not None:
        subscription = subscription.filter(product_id=target_plan.product_id)
    subscription = subscription.first()

## 3) apps/payments/views.py — upgrade_page()

FIND:
        candidates = (Plan.objects
                      .filter(is_active=True, is_trial=False,
                              display_order__gt=subscription.plan.display_order)
                      .order_by("display_order"))

REPLACE WITH:
        candidates = (Plan.objects
                      .filter(is_active=True, is_trial=False,
                              display_order__gt=subscription.plan.display_order)
                      .order_by("display_order"))
        if subscription.plan.product_id is not None:
            candidates = candidates.filter(product_id=subscription.plan.product_id)
        else:
            candidates = candidates.filter(product__isnull=True)
