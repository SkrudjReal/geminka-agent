from pathlib import Path

from app.services.topics import TopicManager


def test_parse_internal_topic_link() -> None:
    assert TopicManager.parse_topic_link("https://t.me/c/4488980222/5") == (
        -1004488980222,
        5,
    )


def test_parse_link_with_message_id_and_direct_forms() -> None:
    assert TopicManager.parse_topic_link("https://t.me/c/1004488980222/17/42") == (
        -1004488980222,
        17,
    )
    assert TopicManager.parse_topic_link("-1004488980222:17") == (
        -1004488980222,
        17,
    )
    assert TopicManager.parse_topic_link("4488980222 17") == (
        -1004488980222,
        17,
    )


def test_topic_registry_persists_and_requires_thread_id(tmp_path: Path) -> None:
    storage = tmp_path / "active_topics.json"
    manager = TopicManager(storage)

    assert not manager.is_topic_active(-1004488980222, None)
    assert not manager.is_topic_active(-1004488980222, 5)

    manager.add_topic(-1004488980222, 5, "Development", 1224362805)
    assert manager.is_topic_active(-1004488980222, 5)

    reloaded = TopicManager(storage)
    assert reloaded.get_active_topics() == manager.get_active_topics()
    assert reloaded.remove_topic(-1004488980222, 5)
    assert not reloaded.is_topic_active(-1004488980222, 5)