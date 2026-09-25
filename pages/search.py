"""문서·이미지 검색(F02) — 근거 기반 질의응답. 근거가 없으면 답변을 유보한다(§4.2)."""

import streamlit as st

from psm.auth import AccessDeniedError
from psm.rag import answer_question
from psm.webapp import current_user_id, get_conn

conn = get_conn()
user_id = current_user_id()

st.title("문서·이미지 검색")

projects = conn.execute("SELECT id, name, dify_dataset_id FROM projects ORDER BY name").fetchall()
if not projects:
    st.info("등록된 사업이 없습니다.")
    st.stop()

project_id = st.selectbox(
    "사업 선택", [p["id"] for p in projects], format_func=lambda pid: next(p["name"] for p in projects if p["id"] == pid)
)
project = next(p for p in projects if p["id"] == project_id)
question = st.text_input("질문")

if st.button("검색", disabled=not question):
    if not project["dify_dataset_id"]:
        st.error("이 사업은 아직 Dify 지식베이스가 연결되지 않았습니다.")
        st.stop()

    try:
        result = answer_question(conn, user_id, project["id"], project["dify_dataset_id"], question)
    except AccessDeniedError:
        st.error("이 사업에 대한 접근 권한이 없습니다.")
        st.stop()

    if result.abstained:
        st.warning(result.abstain_reason)
    else:
        st.write(result.text)
        st.caption("근거: " + ", ".join(e.document_name for e in result.evidence))
        for e in result.evidence:
            if e.is_visual and e.image_path is not None:
                st.image(str(e.image_path), caption=e.document_name, width=300)
