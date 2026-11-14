"""PDFium is not thread-safe: two threads inside it at once corrupt its heap and kill the
process ("malloc(): unaligned tcache chunk detected" in production, from the Inbox loading
thumbnails in parallel). Every call into it must go through one lock."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from app.services import extraction, inbox


class _OverlapDetector:
    """Stands in for pdfium.PdfDocument and records whether two threads were ever inside."""

    def __init__(self) -> None:
        self.inside = 0
        self.overlapped = False
        self.guard = threading.Lock()

    def __call__(self, _data: bytes) -> Any:
        detector = self

        class _Pdf:
            def __init__(self) -> None:
                with detector.guard:
                    detector.inside += 1
                    if detector.inside > 1:
                        detector.overlapped = True
                # Long enough for the other threads to arrive while this one is inside.
                time.sleep(0.02)

            def __len__(self) -> int:
                return 0

            def close(self) -> None:
                with detector.guard:
                    detector.inside -= 1

        return _Pdf()


@pytest.fixture
def detector(monkeypatch: pytest.MonkeyPatch) -> _OverlapDetector:
    fake = _OverlapDetector()
    monkeypatch.setattr(extraction.pdfium, "PdfDocument", fake)
    return fake


def test_thumbnails_never_enter_pdfium_together(detector: _OverlapDetector) -> None:
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda _: inbox._first_page(b"%PDF", is_image=False), range(12)))

    assert not detector.overlapped


def test_page_renders_and_thumbnails_share_the_lock(detector: _OverlapDetector) -> None:
    def work(index: int) -> object:
        if index % 2:
            return extraction.render_pages(b"%PDF")
        return inbox._first_page(b"%PDF", is_image=False)

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(work, range(12)))

    assert not detector.overlapped
