from django.apps import AppConfig


class FeedConfig(AppConfig):
    name = "arkitekt_service.feed"
    label = "arkitekt_feed"
    verbose_name = "Change feed"
    default_auto_field = "django.db.models.BigAutoField"
