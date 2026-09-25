"""사업 현황(F05) — 목록·필터, 실제/계획 진척률, 지연/주의/갱신필요 경고."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import streamlit as st

from psm.alerts import evaluate_alerts
from psm.config import TIMEZONE
from psm.models import Milestone, ProjectInfo
from psm.progress import calc_actual, calc_gap, calc_planned
from psm.webapp import current_user_id, get_conn

conn = get_conn()
user_id = current_user_id()

st.title("사업 현황")

is_admin = conn.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,)).fetchone()["is_admin"]
if is_admin:
    projects = conn.execute("SELECT * FROM projects ORDER BY name").fetchall()
else:
    projects = conn.execute(
        "SELECT p.* FROM projects p JOIN project_members m ON m.project_id = p.id "
        "WHERE m.user_id = ? ORDER BY p.name",
        (user_id,),
    ).fetchall()

if not projects:
    st.info("조회 가능한 사업이 없습니다.")
    st.stop()


def _load_milestones(project_id: int) -> list[Milestone]:
    rows = conn.execute(
        "SELECT m.* FROM milestones m JOIN baselines b ON b.id = m.baseline_id "
        "WHERE m.project_id = ? AND b.is_active = 1",
        (project_id,),
    ).fetchall()
    return [
        Milestone(
            id=row["id"],
            name=row["name"],
            weight=row["weight"],
            due_date=date.fromisoformat(row["due_date"]),
            is_approved=bool(row["is_approved"]),
            last_checked_at=date.fromisoformat(row["last_checked_at"]) if row["last_checked_at"] else None,
        )
        for row in rows
    ]


today = datetime.now(ZoneInfo(TIMEZONE)).date()  # §5 — 경고 판정은 Asia/Seoul 기준
rows = []
for project in projects:
    milestones = _load_milestones(project["id"])
    actual = calc_actual(milestones)
    planned = calc_planned(milestones, today)
    has_owner = (
        conn.execute(
            "SELECT 1 FROM project_members WHERE project_id = ? AND role = 'owner'", (project["id"],)
        ).fetchone()
        is not None
    )
    info = ProjectInfo(
        status=project["status"],
        owner_assigned=has_owner,
        goal=project["goal"],
        has_active_baseline=bool(milestones),
    )
    alerts = evaluate_alerts(info, milestones, today)

    rows.append(
        {
            "사업": project["name"],
            "단계": project["stage"],
            "실제(%)": actual,
            "계획(%)": planned,
            "차이(%p)": calc_gap(actual, planned),
            "경고": ", ".join(a.kind for a in alerts) or "정상",
        }
    )

st.dataframe(rows, width="stretch", hide_index=True)
