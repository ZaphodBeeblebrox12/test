from django.urls import path

from . import views

app_name = "support"

urlpatterns = [
    path("", views.MyTicketsView.as_view(), name="my_tickets"),
    path("request/", views.RequestTicketView.as_view(), name="request_ticket"),
    path("<uuid:pk>/", views.TicketDetailView.as_view(), name="ticket_detail"),
    path("<uuid:pk>/withdraw/", views.WithdrawTicketView.as_view(), name="withdraw_ticket"),
]
