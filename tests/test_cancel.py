"""중단 요청 장부 — 취소와 저장이 동시에 일어나도 둘 중 하나로만 정해진다(취소했는데 저장되거나, 저장됐는데 취소로 보이는 일이 없다)."""

from psm.cancel import CancelRegistry


def test_a_request_cancelled_before_it_is_saved_is_not_saved():
    registry, saved = CancelRegistry(), []

    assert registry.cancel("r1") is True  # 저장되기 전에 취소됨
    ran = registry.run_unless_cancelled("r1", lambda: saved.append("턴"))

    assert ran is False and saved == []


def test_a_request_saved_before_the_cancel_arrives_reports_that_it_was_saved():
    registry, saved = CancelRegistry(), []

    ran = registry.run_unless_cancelled("r1", lambda: saved.append("턴"))
    cancelled_in_time = registry.cancel("r1")

    assert ran is True and saved == ["턴"] and cancelled_in_time is False  # 이미 저장됐다 — 화면이 대화를 다시 읽어야 한다


def test_requests_without_an_id_are_always_saved_and_ids_do_not_affect_each_other():
    registry, saved = CancelRegistry(), []

    assert registry.run_unless_cancelled(None, lambda: saved.append("a")) is True
    registry.cancel("r1")
    assert registry.run_unless_cancelled("r2", lambda: saved.append("b")) is True
    assert saved == ["a", "b"]


def test_the_registry_forgets_old_ids_so_memory_stays_bounded():
    registry = CancelRegistry(limit=3)

    for i in range(10):
        registry.cancel(f"r{i}")

    assert registry.size() <= 3
