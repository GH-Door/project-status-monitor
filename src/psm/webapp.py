"""Streamlit 세션 헬퍼 — DB 연결·로그인 상태 접근. app.py와 pages/*가 공통으로 쓴다."""

from __future__ import annotations

import sqlite3

import streamlit as st

from psm.db import connect, init_db


def get_conn() -> sqlite3.Connection:
    """매 스크립트 실행마다 새 연결을 연다.

    ponytail: @st.cache_resource로 연결 하나를 캐싱했다가, Streamlit이 세션마다 다른 스레드에서
    스크립트를 실행하면서 sqlite3의 "다른 스레드에서 만든 커넥션은 못 쓴다" 오류가 났다. 로컬
    sqlite 파일을 매번 여는 비용은 이 규모(팀원 5명)에서 무시할 만하다 — 캐싱은 실측으로 병목이
    확인되면(logs/app.log) 그때 스레드 안전한 방식(예: connection pool)으로 다시 넣는다.
    """
    conn = connect()
    init_db(conn)
    return conn


def current_user_id() -> int:
    """로그인 안 됐으면 화면을 멈추고 안내한다 — 각 페이지 맨 위에서 호출한다."""
    user_id = st.session_state.get("user_id")
    if user_id is None:
        st.warning("로그인이 필요합니다. 처음 화면에서 로그인하세요.")
        st.stop()
    return user_id
