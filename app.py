"""진입점 — 로그인 게이트 + 4화면 내비게이션(사업 현황/상세/검색/등록·검토, §2)."""

import streamlit as st

from psm.auth import verify_password
from psm.webapp import get_conn

st.set_page_config(page_title="사업 현황 모니터링", layout="wide")


def _login_form() -> None:
    st.title("사업 현황 모니터링")
    with st.form("login"):
        username = st.text_input("아이디")
        password = st.text_input("비밀번호", type="password")
        submitted = st.form_submit_button("로그인")
    if not submitted:
        return

    conn = get_conn()
    user = conn.execute(
        "SELECT id, password_hash, display_name, is_admin FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    if user is None or not verify_password(password, user["password_hash"]):
        st.error("아이디 또는 비밀번호가 올바르지 않습니다")
        return

    st.session_state["user_id"] = user["id"]
    st.session_state["display_name"] = user["display_name"]
    st.rerun()


def main() -> None:
    get_conn()  # 앱 시작 시 DB·스키마 준비

    if "user_id" not in st.session_state:
        _login_form()
        return

    st.sidebar.write(f"{st.session_state['display_name']}님")
    if st.sidebar.button("로그아웃"):
        st.session_state.clear()
        st.rerun()

    pages = [
        st.Page("pages/status.py", title="사업 현황"),
        st.Page("pages/detail.py", title="사업 상세"),
        st.Page("pages/search.py", title="문서·이미지 검색"),
        st.Page("pages/register.py", title="등록·검토"),
    ]
    st.navigation(pages).run()


main()
