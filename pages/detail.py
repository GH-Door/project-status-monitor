"""사업 상세(F03, F04) — 마일스톤 목록, 완료 요청/승인/반려/승인 취소."""

import streamlit as st

from psm import approvals
from psm.auth import AccessDeniedError, require_project_access
from psm.webapp import current_user_id, get_conn

conn = get_conn()
user_id = current_user_id()

st.title("사업 상세")

projects = conn.execute("SELECT id, name FROM projects ORDER BY name").fetchall()
if not projects:
    st.info("등록된 사업이 없습니다.")
    st.stop()

project_id = st.selectbox(
    "사업 선택",
    [p["id"] for p in projects],
    format_func=lambda pid: next(p["name"] for p in projects if p["id"] == pid),
)

try:
    require_project_access(conn, user_id, project_id, action="read")
except AccessDeniedError:
    st.error("이 사업에 대한 접근 권한이 없습니다.")
    st.stop()

can_approve = True
try:
    require_project_access(conn, user_id, project_id, action="approve")
except AccessDeniedError:
    can_approve = False

milestones = conn.execute(
    "SELECT m.* FROM milestones m JOIN baselines b ON b.id = m.baseline_id "
    "WHERE m.project_id = ? AND b.is_active = 1 ORDER BY m.due_date",
    (project_id,),
).fetchall()

if not milestones:
    st.info("이 사업에는 아직 활성 기준선·마일스톤이 없습니다.")
    st.stop()

for m in milestones:
    status = "승인됨" if m["is_approved"] else "미승인"
    with st.expander(f"{m['name']} — 가중치 {m['weight']} · 기한 {m['due_date']} · {status}"):
        if not can_approve:
            st.caption("조회자는 승인 관련 조작을 할 수 없습니다.")
            continue

        col1, col2, col3 = st.columns(3)
        if col1.button("완료 요청", key=f"req-{m['id']}"):
            approvals.request_completion(conn, m["id"], user_id)
            st.rerun()

        if col2.button("승인", key=f"appr-{m['id']}"):
            try:
                approvals.approve(conn, m["id"], user_id, expected_version=m["version"])
                st.rerun()
            except approvals.ConcurrentModificationError:
                st.error("다른 사용자가 먼저 변경했습니다. 새로고침 후 다시 시도하세요.")
            except approvals.AlreadyApprovedError:
                st.warning("이미 승인된 마일스톤입니다.")

        if col3.button("승인 취소", key=f"revoke-{m['id']}"):
            try:
                approvals.revoke(
                    conn, m["id"], user_id, reason="담당자 취소", expected_version=m["version"]
                )
                st.rerun()
            except approvals.NotApprovedError:
                st.warning("승인 상태가 아닙니다.")
            except approvals.ConcurrentModificationError:
                st.error("다른 사용자가 먼저 변경했습니다. 새로고침 후 다시 시도하세요.")
