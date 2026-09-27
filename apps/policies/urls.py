from django.urls import path

from . import views

urlpatterns = [
    path("<str:policy_type>/", views.policy_page, name="policy-page"),
    path("<str:policy_type>/v/<str:version>/", views.policy_version_page,
         name="policy-version-page"),
]
