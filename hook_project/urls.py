from arkitekt_service.hook import HookAgent

#: The agent under test: a process that is no service. Tests declare on it and clear it again.
agent = HookAgent("worker", description="The agent under test.")

urlpatterns = [*agent.urls]
