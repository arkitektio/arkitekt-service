from arkitekt_service.service import Service

#: The service under test. Tests declare on it and clear it again.
service = Service("testsvc", description="The service under test.")

urlpatterns = [*service.urls]
