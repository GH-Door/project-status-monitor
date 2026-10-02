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


def test_retrieve_without_documents_sends_no_metadata_filter(monkeypatch):
    stub_client = _StubClient(_StubResponse(200, {"records": []}))
    monkeypatch.setattr(dify, "_client", lambda: stub_client)

    dify.retrieve("dataset-1", "질문", top_k=5)

    assert "metadata_filtering_conditions" not in stub_client.last_payload


def test_retrieve_with_documents_filters_by_document_name(monkeypatch):
    stub_client = _StubClient(_StubResponse(200, {"records": []}))
    monkeypatch.setattr(dify, "_client", lambda: stub_client)

    dify.retrieve("dataset-1", "질문", top_k=5, document_names=["asset:3", "asset:7"])

    assert stub_client.last_payload["metadata_filtering_conditions"] == {
        "logical_operator": "or",
        "conditions": [
            {"name": "document_name", "comparison_operator": "is", "value": "asset:3"},
            {"name": "document_name", "comparison_operator": "is", "value": "asset:7"},
        ],
    }


def test_create_dataset_enables_builtin_metadata_and_returns_id(monkeypatch):
    class _Client(_StubClient):
        def __init__(self):
            super().__init__(_StubResponse(200, {"id": "ds-9"}))
            self.urls = []

        def post(self, url, json=None):
            self.urls.append(url)
            return self._response

    stub_client = _Client()
    monkeypatch.setattr(dify, "_client", lambda: stub_client)

    assert dify.create_dataset("누베른코스") == "ds-9"
    assert stub_client.urls == ["/datasets", "/datasets/ds-9/metadata/built-in/enable"]


def test_document_filter_is_sent_inside_retrieval_model_as_well(monkeypatch):
    """Dify 버전에 따라 필터 위치가 다를 수 있어 두 곳에 같은 값을 보낸다."""
    stub_client = _StubClient(_StubResponse(200, {"records": []}))
    monkeypatch.setattr(dify, "_client", lambda: stub_client)

    dify.retrieve("dataset-1", "질문", top_k=5, document_names=["asset:3"])

    payload = stub_client.last_payload
    assert payload["retrieval_model"]["metadata_filtering_conditions"] == payload["metadata_filtering_conditions"]
