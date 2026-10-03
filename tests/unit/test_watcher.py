import queue
import threading
import time
from pathlib import Path

from watchdog.events import FileCreatedEvent, FileMovedEvent

from biblio_demon.watcher import InboxHandler, is_candidate_pdf, wait_until_stable


def test_is_candidate_pdf() -> None:
    assert is_candidate_pdf(Path("a.PDF"))
    assert not is_candidate_pdf(Path("._a.pdf"))
    assert not is_candidate_pdf(Path("a.pdf.crdownload"))


def test_handler_filters_events(tmp_path: Path) -> None:
    q: queue.Queue[Path] = queue.Queue()
    handler = InboxHandler(tmp_path, q)
    handler.on_created(FileCreatedEvent(str(tmp_path / "a.pdf")))
    handler.on_created(FileCreatedEvent(str(tmp_path / "a.txt")))
    handler.on_created(FileCreatedEvent(str(tmp_path / "manual_review" / "b.pdf")))
    handler.on_moved(
        FileMovedEvent(str(tmp_path / "c.crdownload"), str(tmp_path / "c.pdf"))
    )
    assert [q.get_nowait().name for _ in range(q.qsize())] == ["a.pdf", "c.pdf"]


def test_wait_until_stable_waits_for_complete_pdf(tmp_path: Path, make_pdf) -> None:
    full = make_pdf(tmp_path / "full.pdf", ["hello"]).read_bytes()
    target = tmp_path / "growing.pdf"
    target.write_bytes(full[: len(full) // 2])

    def finish() -> None:
        time.sleep(0.3)
        target.write_bytes(full)

    threading.Thread(target=finish).start()
    assert wait_until_stable(target, checks=2, interval=0.1, timeout=5)


def test_wait_until_stable_missing_file(tmp_path: Path) -> None:
    assert not wait_until_stable(tmp_path / "nope.pdf", interval=0.01, timeout=1)
