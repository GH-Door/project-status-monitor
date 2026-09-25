"""등록·검토(F01) — 문서·이미지 업로드 → 검사 → 전처리·색인."""

from pathlib import Path

import streamlit as st

from psm.auth import AccessDeniedError
from psm.config import DATA_DIR
from psm.ingest import IngestRejected, ingest_and_index
from psm.webapp import current_user_id, get_conn

conn = get_conn()
user_id = current_user_id()

st.title("문서·이미지 등록")

projects = conn.execute("SELECT id, name, dify_dataset_id FROM projects ORDER BY name").fetchall()
if not projects:
    st.info("등록된 사업이 없습니다.")
    st.stop()

project_id = st.selectbox(
    "사업 선택", [p["id"] for p in projects], format_func=lambda pid: next(p["name"] for p in projects if p["id"] == pid)
)
project = next(p for p in projects if p["id"] == project_id)
uploaded = st.file_uploader("파일", type=["txt", "md", "pdf", "png", "jpg", "jpeg", "webp"])
export_approved = st.checkbox("외부 API 반출 승인 완료")  # §6 — 승인 없이는 등록 자체를 막는다

if st.button("등록", disabled=uploaded is None):
    if not project["dify_dataset_id"]:
        st.error("이 사업은 아직 Dify 지식베이스가 연결되지 않았습니다.")
        st.stop()

    safe_name = Path(uploaded.name).name  # 경로 조작 방지 — 디렉터리 구성요소를 제거하고 파일명만 남긴다
    tmp_path = DATA_DIR / "uploads" / safe_name
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path.write_bytes(uploaded.getvalue())

    try:
        document_id = ingest_and_index(
            conn,
            project["id"],
            user_id,
            project["dify_dataset_id"],
            tmp_path,
            uploaded.type or "application/octet-stream",
            export_approved=export_approved,
        )
    except (IngestRejected, AccessDeniedError) as error:
        st.error(str(error))
    else:
        st.success(f"등록·색인 완료 (document_id={document_id}).")
