"""progress.py/alerts.py/approvals.py가 공유하는 최소 데이터 구조."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class Milestone:
    id: int
    name: str
    weight: float
    due_date: date
    is_approved: bool
    last_checked_at: date | None = None


@dataclass(frozen=True)
class Alert:
    kind: str  # "지연" | "주의" | "갱신필요" | "정보부족"
    reason: str


@dataclass(frozen=True)
class EvidenceItem:
    """검색으로 찾은 근거 1건 — 서버 DB로 대조·검증까지 마친 상태여야 한다(rag.py)."""

    asset_id: int
    document_name: str
    content: str
    is_visual: bool
    image_path: Path | None = None


@dataclass(frozen=True)
class ProjectInfo:
    """경고 판정에 필요한 최소 사업 정보. status가 active가 아니면 경고를 집계하지 않는다(§5)."""

    status: str  # "active" | "paused" | "closed"
    owner_assigned: bool
    goal: str | None
    has_active_baseline: bool
