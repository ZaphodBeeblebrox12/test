"""User-facing views for support tickets."""
import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, DetailView, ListView, View

from .forms import SupportTicketForm
from .services import send_ticket_confirmation
from .models import MAX_ATTACHMENTS_PER_TICKET, SupportTicket, TicketAttachment

logger = logging.getLogger(__name__)


class RequestTicketView(LoginRequiredMixin, CreateView):
    """Raise a support ticket (access request / billing / technical)."""
    model = SupportTicket
    form_class = SupportTicketForm
    template_name = "support/request_ticket.html"
    success_url = reverse_lazy("support:my_tickets")

    def dispatch(self, request, *args, **kwargs):
        existing = SupportTicket.objects.filter(
            user=request.user, status=SupportTicket.Status.PENDING).first()
        if existing:
            messages.info(request, "You already have a pending ticket - "
                                   "we'll get to it as soon as possible.")
            return HttpResponseRedirect(
                reverse_lazy("support:ticket_detail", kwargs={"pk": existing.pk}))
        return super().dispatch(request, *args, **kwargs)

    @transaction.atomic
    def form_valid(self, form):
        form.instance.user = self.request.user
        try:
            self.object = form.save()
        except IntegrityError:
            form.add_error(None, "You already have a pending ticket.")
            return self.form_invalid(form)
        files = form.cleaned_data.get("attachments") or []
        for f in files[:MAX_ATTACHMENTS_PER_TICKET]:
            TicketAttachment(ticket=self.object, file=f).save()
        send_ticket_confirmation(self.object)
        messages.success(self.request,
                         "Ticket submitted. We'll review it and get back to you.")
        return HttpResponseRedirect(self.get_success_url())


class MyTicketsView(LoginRequiredMixin, ListView):
    model = SupportTicket
    template_name = "support/my_tickets.html"
    context_object_name = "tickets"
    paginate_by = 20

    def get_queryset(self):
        return (SupportTicket.objects
                .filter(user=self.request.user)
                .select_related("affiliate_link", "plan")
                .prefetch_related("attachments"))


class TicketDetailView(LoginRequiredMixin, DetailView):
    model = SupportTicket
    template_name = "support/ticket_detail.html"
    context_object_name = "ticket"

    def get_queryset(self):
        return (SupportTicket.objects
                .select_related("affiliate_link", "plan", "reviewed_by")
                .prefetch_related("attachments"))

    def dispatch(self, request, *args, **kwargs):
        obj = self.get_object()
        is_owner = obj.user_id == request.user.id
        if not (is_owner or request.user.is_staff):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class WithdrawTicketView(LoginRequiredMixin, View):
    """Let a user close (withdraw) their own pending ticket."""
    http_method_names = ["post"]

    def post(self, request, pk):
        ticket = get_object_or_404(SupportTicket, pk=pk)
        if ticket.user_id != request.user.id:
            raise PermissionDenied
        if ticket.status != SupportTicket.Status.PENDING:
            messages.error(request, "Only pending tickets can be withdrawn.")
            return redirect("support:ticket_detail", pk=pk)
        ticket.status = SupportTicket.Status.WITHDRAWN
        ticket.save(update_fields=["status", "updated_at"])
        messages.success(request, "Ticket withdrawn.")
        return redirect("support:ticket_detail", pk=pk)
