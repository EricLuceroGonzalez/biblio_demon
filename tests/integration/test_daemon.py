"""El demonio detecta un PDF nuevo, lo archiva y deja el resto en revisión."""

import threading
import time
from pathlib import Path

from biblio_demon.pipeline import Pipeline
from biblio_demon.state import StateStore
from biblio_demon.watcher import InboxWatcher
from tests.unit.test_pipeline import RESNET_PAGE, FakeNotion, FakeResolver, FakeZotero


def _wait_for(predicate, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def test_daemon_end_to_end(settings, resnet_meta, make_pdf, tmp_path: Path) -> None:
    settings.stable_checks = 1
    settings.stable_interval = 0.1
    zotero, notion = FakeZotero(), FakeNotion()
    pipeline = Pipeline(
        settings,
        resolver=FakeResolver({resnet_meta.doi: resnet_meta}),  # type: ignore[arg-type]
        state=StateStore(settings.state_db),
        zotero_sync=zotero,  # type: ignore[arg-type]
        notion_sync=notion,  # type: ignore[arg-type]
    )
    # Uno ya presente al arrancar, otro que llega después vía "mover".
    make_pdf(settings.inbox_path / "preexisting.pdf", ["nothing here"])
    watcher = InboxWatcher(pipeline)
    thread = threading.Thread(target=watcher.run, daemon=True)
    thread.start()
    try:
        staged = make_pdf(tmp_path / "download.crdownload", RESNET_PAGE)
        time.sleep(0.5)
        staged.rename(settings.inbox_path / "download.pdf")

        archived = settings.archive_path / (
            "(2016)-Deep_Residual_Learning_for_Image_Recognition-(He-EtAl).pdf"
        )
        assert _wait_for(archived.exists)
        assert _wait_for(
            lambda: (settings.manual_review_path / "preexisting.pdf").exists()
        )
        assert _wait_for(lambda: notion.synced == [resnet_meta.doi])
        assert zotero.synced[0][1] == archived
    finally:
        watcher.stop()
        thread.join(timeout=10)
