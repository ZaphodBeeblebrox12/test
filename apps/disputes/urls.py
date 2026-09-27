from django.urls import path

from . import views

app_name = "disputes"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("<uuid:pk>/", views.detail, name="detail"),
    path("<uuid:pk>/note/", views.add_note, name="note"),
    path("<uuid:pk>/generate/", views.generate, name="generate"),
    path("<uuid:pk>/submit/", views.submit_latest, name="submit"),
    path("<uuid:pk>/resolve/", views.resolve, name="resolve"),
]
