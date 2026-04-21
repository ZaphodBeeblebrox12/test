from django.urls import path
from . import views

urlpatterns = [
    path('', views.LandingPageView.as_view(), name='landing'),
    # path('login/', views.CustomLoginView.as_view(), name='custom_login'),
]
