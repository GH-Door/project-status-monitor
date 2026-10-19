from pathlib import Path

import pytest

from psm import dify, index_reuse, ingest, library, rag


@pytest.fixture
def source(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "ORIGINALS_DIR", tmp_path / "originals")
    conn.execute("INSERT INTO users (id,username,password_hash,display_name,is_admin) VALUES (1,'u','!','U',1)")
    for pid in (1, 2, 3):
        conn.execute("INSERT INTO projects (id,name,dify_dataset_id) VALUES (?,?,?)", (pid, f"회사{pid}", f"ds-{pid}"))
    conn.commit()
    calls = []
    monkeypatch.setattr(dify, "add_text_document", lambda ds, aid, text: calls.append((ds, aid)) or f"remote-{aid}")
    path = tmp_path / "원문.md"
    path.write_text("기업의 승인된 비용은 123원입니다. 동일 원문 색인 재사용 검증 자료입니다.", encoding="utf-8")
    doc, _ = library.index_file(conn, 1, 1, "ds-1", path, "text/markdown")
    conn.execute("UPDATE documents SET kpi_extracted_at='2026-10-01' WHERE id=?", (doc,))
    conn.execute(
        "INSERT INTO business_metrics (project_id,document_id,key,value,unit,evidence_text,status,source) "
        "VALUES (1,?,'비용',123,'원','123원','approved','llm')", (doc,),
    )
    conn.commit()
    return path, doc, calls


def test_reuses_index_and_metrics_without_external_registration(conn, source):
    path, old_doc, calls = source
    old_assets = [tuple(r) for r in conn.execute("SELECT * FROM assets WHERE project_id=1")]
    new_doc, is_new = library.index_file(conn, 2, 1, "ds-2", path, "text/markdown")
    assert is_new and new_doc != old_doc
    assert len(calls) == 1  # 최초 원문만 외부 등록한다.
    assert [tuple(r) for r in conn.execute("SELECT * FROM assets WHERE project_id=1")] == old_assets
    assert conn.execute("SELECT COUNT(*) FROM asset_index_links").fetchone()[0] == 1
    assert conn.execute("SELECT value FROM business_metrics WHERE project_id=2").fetchone()[0] == 123
    assert conn.execute("SELECT kpi_extracted_at FROM documents WHERE id=?", (new_doc,)).fetchone()[0]
    assert ingest.document_storage_path(conn, new_doc).read_bytes() == path.read_bytes()
    assert library.index_file(conn, 2, 1, "ds-2", path, "text/markdown") == (new_doc, False)
    assert len(calls) == 1


def test_linked_retrieval_filters_before_and_after_dify(conn, source, monkeypatch):
    path, old_doc, _ = source
    new_doc, _ = library.index_file(conn, 2, 1, "ds-2", path, "text/markdown")
    source_id = conn.execute("SELECT id FROM assets WHERE document_id=?", (old_doc,)).fetchone()[0]
    local_id = conn.execute("SELECT id FROM assets WHERE document_id=?", (new_doc,)).fetchone()[0]
    calls = []

    def retrieve(ds, question, top_k, document_names=None):
        calls.append((ds, document_names))
        return [{"document_name": f"asset:{aid}", "content": "123원", "score": 1} for aid in (source_id, 999999)]

    monkeypatch.setattr(dify, "retrieve", retrieve)
    evidence = rag.retrieve_evidence(conn, 2, "ds-2", "비용은?")
    assert [e.asset_id for e in evidence] == [local_id]
    assert calls == [("ds-1", [f"asset:{source_id}"])]
    assert rag.retrieve_evidence(conn, 2, "ds-2", "비용은?", [old_doc]) == []


def test_index_not_reused_without_source_permission_or_when_incomplete(conn, source):
    path, _, calls = source
    conn.execute("UPDATE users SET is_admin=0 WHERE id=1")
    conn.execute("INSERT INTO project_members VALUES (2,1,'owner')")
    conn.commit()
    library.index_file(conn, 2, 1, "ds-2", path, "text/markdown")
    assert len(calls) == 2 and not conn.execute("SELECT * FROM asset_index_links").fetchall()
    conn.execute("UPDATE users SET is_admin=1 WHERE id=1")
    conn.execute("UPDATE assets SET status='pending'")
    conn.commit()
    library.index_file(conn, 3, 1, "ds-3", path, "text/markdown")
    assert len(calls) == 3


def test_copy_preserves_target_values_and_aliases_are_canonical(conn, source):
    path, old_doc, _ = source
    new_doc, _ = library.index_file(conn, 2, 1, "ds-2", path, "text/markdown")
    conn.execute("UPDATE business_metrics SET value=456,status='rejected' WHERE project_id=2")
    conn.commit()
    src = conn.execute("SELECT * FROM documents WHERE id=?", (old_doc,)).fetchone()
    dst = conn.execute("SELECT * FROM documents WHERE id=?", (new_doc,)).fetchone()
    with conn:
        assert index_reuse.link_existing(conn, src, dst) == 1
    assert tuple(conn.execute("SELECT value,status FROM business_metrics WHERE project_id=2").fetchone()) == (456, "rejected")
    library.index_file(conn, 3, 1, "ds-3", path, "text/markdown")
    links = conn.execute("SELECT source_asset_id,dataset_id FROM asset_index_links").fetchall()
    assert len(links) == 2 and tuple(links[0]) == tuple(links[1])


def test_new_content_gets_its_own_index_and_mixed_retrieval_stays_scoped(conn, source, monkeypatch):
    path, _, calls = source
    reused, _ = library.index_file(conn, 2, 1, "ds-2", path, "text/markdown")
    other = Path(path).with_name("새자료.md")
    other.write_text("다른 내용의 신규 문서는 새로운 색인을 만들어야 합니다.", encoding="utf-8")
    own, _ = library.index_file(conn, 2, 1, "ds-2", other, "text/markdown")
    assert len(calls) == 2
    queried = []

    def retrieve(ds, question, top_k, document_names=None):
        queried.append(ds)
        return [{"document_name": name, "content": "근거", "score": 1} for name in document_names]

    monkeypatch.setattr(dify, "retrieve", retrieve)
    assert len(rag.retrieve_evidence(conn, 2, "ds-2", "질문")) == 2
    assert set(queried) == {"ds-1", "ds-2"}
    queried.clear()
    assert len(rag.retrieve_evidence(conn, 2, "ds-2", "질문", [own])) == 1
    assert queried == ["ds-2"]
    assert reused != own
