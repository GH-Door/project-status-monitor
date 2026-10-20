"""중단 요청 장부 — 화면에서 "중단"을 눌렀을 때 그 요청의 답이 대화에 저장되지 않게 한다.

취소와 저장이 거의 동시에 일어나도 락 하나 안에서 둘 중 하나로 정해진다: 취소가 먼저면 저장하지 않고,
저장이 먼저면 취소는 "이미 저장됨"을 돌려줘 화면이 대화를 다시 읽게 한다. 서버가 이미 시작한 OpenAI 호출 자체는 멈추지 못한다(비용은 나갈 수 있다).
프로세스 메모리에만 둔다 — 서버가 하나일 때의 장치이고, 오래된 요청 번호는 limit을 넘으면 잊는다.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable


class CancelRegistry:
    def __init__(self, limit: int = 1000) -> None:
        self._limit = limit
        self._lock = threading.Lock()
        self._cancelled: OrderedDict[str, None] = OrderedDict()
        self._saved: OrderedDict[str, None] = OrderedDict()

    def _remember(self, bucket: OrderedDict[str, None], key: str) -> None:
        bucket[key] = None
        while len(bucket) > self._limit:
            bucket.popitem(last=False)

    def cancel(self, key: str) -> bool:
        """저장되기 전에 취소를 기록했으면 True, 이미 저장된 요청이면 False."""
        with self._lock:
            if key in self._saved:
                return False
            self._remember(self._cancelled, key)
            return True

    def run_unless_cancelled(self, key: str | None, save: Callable[[], object]) -> bool:
        """취소되지 않았을 때만 save()를 실행한다. 실행했으면 True. key가 없으면 항상 실행한다."""
        if key is None:
            save()
            return True
        with self._lock:
            if key in self._cancelled:
                return False
            save()
            self._remember(self._saved, key)
            return True

    def size(self) -> int:
        with self._lock:
            return len(self._cancelled) + len(self._saved)
