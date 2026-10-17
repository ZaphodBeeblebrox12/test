from django.urls import path

from . import views

app_name = "academy"

urlpatterns = [
    path("", views.academy_home, name="home"),
    path("course/<slug:course_slug>/", views.course_detail, name="course-detail"),
    path("course/<slug:course_slug>/lesson/<uuid:lesson_id>/", views.lesson_detail, name="lesson"),
    path("lesson/<uuid:lesson_id>/progress/", views.lesson_progress, name="lesson-progress"),
    path("media/<uuid:asset_id>/", views.media_asset, name="media-asset"),
]
