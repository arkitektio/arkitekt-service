from arkitekt_service.feed.publisher import FeedPublisher

#: The producer under test: one feed, read by kraph. Tests set its render.
publisher = FeedPublisher("live.arkitekt.mikro")
publisher.feed("@mikro/metric", readers=["live.arkitekt.kraph"], render=lambda entries: {e.object: {"seen": e.object} for e in entries})

urlpatterns = [*publisher.urls]
