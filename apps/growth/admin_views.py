"""
Admin views for sending gifts.

Simple UI for non-technical staff to send gift subscriptions.
"""
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.shortcuts import render, redirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View

from apps.subscriptions.models import Plan
from .services import GiftService, GiftEmailService
from .forms import AdminGiftSendForm


@method_decorator(staff_member_required, name='dispatch')
class SendGiftAdminView(View):
    """
    Admin view for sending gifts.

    URL: /admin/growth/send-gift/
    """
    template_name = "admin/growth/send_gift.html"

    def get(self, request):
        """Show the send gift form."""
        plans = Plan.objects.filter(is_active=True).order_by('name')
        form = AdminGiftSendForm()
        form.fields['plan'].queryset = plans

        return render(request, self.template_name, {
            'form': form,
            'title': 'Send Gift',
            'opts': {'app_label': 'growth', 'model_name': 'sendgift'},
        })

    def post(self, request):
        """Process the gift sending."""
        plans = Plan.objects.filter(is_active=True).order_by('name')
        form = AdminGiftSendForm(request.POST)
        form.fields['plan'].queryset = plans

        if form.is_valid():
            try:
                gift_sub, gift_invite = GiftService.create_gift(
                    from_user=request.user,
                    recipient_email=form.cleaned_data['recipient_email'],
                    plan=form.cleaned_data['plan'],
                    duration_days=form.cleaned_data['duration_days'],
                    message=form.cleaned_data.get('message', ''),
                    request=request,
                )

                claim_url = request.build_absolute_uri(
                    reverse('growth:claim', kwargs={'token': gift_invite.claim_token})
                )

                email_sent = GiftEmailService.send_gift_email(
                    gift_invite=gift_invite,
                    claim_url=claim_url,
                )

                if email_sent:
                    messages.success(
                        request,
                        f"Gift sent to {gift_invite.recipient_email} successfully!"
                    )
                else:
                    messages.warning(
                        request,
                        f"Gift created but email failed to send. Claim URL: {claim_url}"
                    )

                return redirect('growth_admin:growth_send_gift_success',
                               gift_invite_id=gift_invite.id)

            except Exception as e:
                messages.error(request, f"Error sending gift: {str(e)}")

        return render(request, self.template_name, {
            'form': form,
            'title': 'Send Gift',
            'opts': {'app_label': 'growth', 'model_name': 'sendgift'},
        })


@method_decorator(staff_member_required, name='dispatch')
class SendGiftSuccessAdminView(View):
    """
    Success page after sending a gift.
    """
    template_name = "admin/growth/send_gift_success.html"

    def get(self, request, gift_invite_id):
        """Show success page with claim URL."""
        from .models import GiftInvite

        try:
            gift_invite = GiftInvite.objects.select_related('gift_subscription').get(
                id=gift_invite_id
            )
        except GiftInvite.DoesNotExist:
            messages.error(request, "Gift not found.")
            return redirect('growth_admin:growth_send_gift')

        claim_url = request.build_absolute_uri(
            reverse('growth:claim', kwargs={'token': gift_invite.claim_token})
        )

        return render(request, self.template_name, {
            'title': 'Gift Sent',
            'gift_invite': gift_invite,
            'claim_url': claim_url,
            'recipient_email': gift_invite.recipient_email,
            'plan_name': gift_invite.gift_subscription.plan.name,
            'duration_days': gift_invite.gift_subscription.duration_days,
            'opts': {'app_label': 'growth', 'model_name': 'sendgift'},
        })
