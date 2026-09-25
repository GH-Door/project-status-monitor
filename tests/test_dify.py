"""Dify 클라이언트 테스트 — 네트워크 없이 요청/응답 매핑 로직만 검증."""

from psm import dify


def test_asset_id_from_document_name_parses_prefix():
    assert dify.asset_id_from_document_name("asset:42") == 42


def test_asset_id_from_document_name_rejects_foreign_documents():
    """서버가 만들지 않은 문서명은 근거로 신뢰하지 않는다(§5, AI가 ID를 만들게 하지 않는다)."""
    assert dify.asset_id_from_document_name("random_upload.txt") is None
    assert dify.asset_id_from_document_name("asset:not-a-number") is None


class _StubResponse:
    def __init__(self, status_code, json_body, text=""):
        self.status_code = status_code
        self._json_body = json_body
        self.text = text

    def json(self):
        return self._json_body


class _StubClient:
    def __init__(self, response):
        self._response = response
        self.last_payload = None
        self.last_url = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, json):
        self.last_url = url
        self.last_payload = json
        return self._response


def test_retrieve_maps_records_and_truncates_query(monkeypatch):
    response = _StubResponse(
        200,
        {
            "records": [
                {"segment": {"content": "본문1", "document": {"name": "asset:1"}}, "score": 0.9},
                {"segment": {"content": "본문2", "document": {"name": "asset:2"}}, "score": 0.8},
            ]
        },
    )
    stub_client = _StubClient(response)
    monkeypatch.setattr(dify, "_client", lambda: stub_client)

    long_query = "질문 " * 200  # 250자 제한 초과
    results = dify.retrieve("dataset-1", long_query, top_k=5)

    assert results == [
        {"document_name": "asset:1", "content": "본문1", "score": 0.9},
        {"document_name": "asset:2", "content": "본문2", "score": 0.8},
    ]
    assert len(stub_client.last_payload["query"]) <= 250
    assert stub_client.last_url == "/datasets/dataset-1/retrieve"


def test_retrieve_raises_on_error_status(monkeypatch):
    stub_client = _StubClient(_StubResponse(500, {}, text="internal error"))
    monkeypatch.setattr(dify, "_client", lambda: stub_client)

    try:
        dify.retrieve("dataset-1", "질문", top_k=5)
        assert False, "DifyError가 발생해야 합니다"
    except dify.DifyError:
        pass
