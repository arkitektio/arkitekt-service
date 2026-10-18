from django.apps import AppConfig


class SpacesConfig(AppConfig):
    name = "arkitekt_service.collaborations"
    label = "arkitekt_collaborations"
    verbose_name = "Collaborations"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from django.db.models.signals import post_delete, post_save

        from arkitekt_service.collaborations.guests import changed
        from arkitekt_service.collaborations.models import CollaborationItem

        post_save.connect(changed, sender=CollaborationItem, dispatch_uid="arkitekt-collaborations-changed-save")
        post_delete.connect(changed, sender=CollaborationItem, dispatch_uid="arkitekt-collaborations-changed-delete")
