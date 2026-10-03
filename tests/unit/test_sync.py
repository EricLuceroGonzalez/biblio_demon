from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from biblio_demon.sync import SyncError
from biblio_demon.sync.notion_sync import NotionSync, _rich_text
from biblio_demon.sync.zotero_sync import ZoteroSync


class FakeZot:
    def __init__(self, existing: list[dict[str, Any]] | None = None) -> None:
        self.existing = existing or []
        self.created: list[tuple[list[dict[str, Any]], str | None]] = []
        self.children_items: list[dict[str, Any]] = []

    def items(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.existing

    def children(self, key: str) -> list[dict[str, Any]]:
        return self.children_items

    def item_template(self, itemtype: str, linkmode: str | None = None):
        if itemtype == "attachment":
            return {
                "itemType": "attachment",
                "linkMode": linkmode,
                "title": "",
                "path": "",
                "contentType": "",
            }
        base = {
            "itemType": itemtype,
            "title": "",
            "creators": [],
            "date": "",
            "DOI": "",
            "url": "",
            "abstractNote": "",
            "collections": [],
        }
        if itemtype == "conferencePaper":
            base["proceedingsTitle"] = ""
        return base

    def create_items(self, payload, parentid=None):
        self.created.append((payload, parentid))
        return {"successful": {"0": {"key": f"K{len(self.created)}"}}, "failed": {}}


def test_zotero_creates_item_and_linked_pdf(settings, resnet_meta, tmp_path) -> None:
    zot = FakeZot()
    settings.zotero_collection_key = "COLL"
    key = ZoteroSync(settings, client=zot).sync(resnet_meta, tmp_path / "a.pdf")
    assert key == "K1"
    item, parent = zot.created[0]
    assert parent is None
    assert item[0]["proceedingsTitle"].startswith("2016 IEEE")
    assert item[0]["creators"][0] == {
        "creatorType": "author",
        "firstName": "Kaiming",
        "lastName": "He",
    }
    assert item[0]["collections"] == ["COLL"]
    assert "DOI" in item[0] and item[0]["DOI"] == resnet_meta.doi
    att, parent = zot.created[1]
    assert parent == "K1"
    assert att[0]["linkMode"] == "linked_file"
    assert att[0]["path"] == str((tmp_path / "a.pdf").resolve())


def test_zotero_reuses_existing_item(settings, resnet_meta, tmp_path) -> None:
    pdf = (tmp_path / "a.pdf").resolve()
    zot = FakeZot(
        existing=[
            {"key": "ATT", "data": {"itemType": "attachment", "DOI": resnet_meta.doi}},
            {
                "key": "OLD",
                "data": {"itemType": "conferencePaper", "DOI": resnet_meta.doi.upper()},
            },
        ]
    )
    zot.children_items = [{"data": {"linkMode": "linked_file", "path": str(pdf)}}]
    assert ZoteroSync(settings, client=zot).sync(resnet_meta, pdf) == "OLD"
    assert zot.created == []


class FakeNotionClient:
    def __init__(self, props: dict[str, Any], existing: list[Any] | None = None):
        outer = self

        class DB:
            def retrieve(self, database_id):
                return {"data_sources": [{"id": "ds1", "name": "Main"}]}

        class DS:
            def retrieve(self, data_source_id):
                return {"properties": props}

            def query(self, data_source_id, **kw):
                outer.queries.append(kw)
                return {"results": existing or []}

        class Pages:
            def create(self, **kw):
                outer.created.append(kw)
                return {"id": "new-page"}

        self.databases, self.data_sources, self.pages = DB(), DS(), Pages()
        self.created: list[dict[str, Any]] = []
        self.queries: list[dict[str, Any]] = []


GOOD_SCHEMA = {
    "Name": {"type": "title"},
    "DOI": {"type": "url"},
    "Año": {"type": "number"},
    "Estado": {"type": "status", "status": {"options": [{"name": "Inbox"}]}},
    "Autores": {"type": "rich_text"},
}


def _notion_settings(settings):
    settings.notion_token = SecretStr("t")
    settings.notion_database_id = "db"
    return settings


def test_notion_creates_page(settings, resnet_meta) -> None:
    client = FakeNotionClient(GOOD_SCHEMA)
    sync = NotionSync(_notion_settings(settings), client=client)
    sync.validate_schema()
    assert sync.sync(resnet_meta) == "new-page"
    created = client.created[0]
    assert created["parent"] == {"type": "data_source_id", "data_source_id": "ds1"}
    props = created["properties"]
    assert props["Name"]["title"][0]["text"]["content"] == (
        "Deep Residual Learning for Image Recognition (He, 2016)"
    )
    assert props["DOI"] == {"url": "https://doi.org/10.1109/cvpr.2016.90"}
    assert props["Año"] == {"number": 2016}
    assert props["Estado"] == {"status": {"name": "Inbox"}}
    assert (
        "Kaiming He, Xiangyu Zhang"
        in props["Autores"]["rich_text"][0]["text"]["content"]
    )


def test_notion_skips_existing(settings, resnet_meta) -> None:
    client = FakeNotionClient(GOOD_SCHEMA, existing=[{"id": "old"}])
    assert (
        NotionSync(_notion_settings(settings), client=client).sync(resnet_meta) == "old"
    )
    assert client.created == []


def test_notion_schema_errors(settings) -> None:
    bad = dict(GOOD_SCHEMA, Estado={"type": "select"})
    del bad["Autores"]
    sync = NotionSync(_notion_settings(settings), client=FakeNotionClient(bad))
    with pytest.raises(SyncError, match="Estado.*status.*Autores.*ausente"):
        sync.validate_schema()


def test_rich_text_chunks() -> None:
    chunks = _rich_text("x" * 4500)
    assert [len(c["text"]["content"]) for c in chunks] == [2000, 2000, 500]


def test_disabled_without_credentials(settings) -> None:
    with pytest.raises(ValueError):
        ZoteroSync(settings)
    with pytest.raises(ValueError):
        NotionSync(settings)
    assert not settings.zotero_enabled and not settings.notion_enabled
    assert isinstance(Path(settings.state_db), Path)
