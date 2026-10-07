from django.apps import AppConfig


class ServerConfig(AppConfig):
    name = "arkitekt_service.server"
    label = "arkitekt_service_server"
    verbose_name = "Arkitekt service"

    def ready(self) -> None:
        """Register the system checks."""
        from arkitekt_service.server import checks  # noqa: F401
