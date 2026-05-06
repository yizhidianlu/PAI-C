"""pdf_extract tests — §25.

We mock the ``pypdf.PdfReader`` surface so the suite never depends on a
real PDF on disk. The pdf_extract module imports pypdf lazily inside
``extract_pdf_text``, so we monkeypatch ``sys.modules`` accordingly.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from paic.sources.pdf_extract import _content_hash, extract_pdf_text


def _install_fake_pypdf(monkeypatch, *, pages=None, encrypted=False, raise_corrupt=False):
    """Install a fake ``pypdf`` module into sys.modules for the test.

    ``pages`` is a list of page texts the fake reader will iterate.
    ``encrypted`` flips the .is_encrypted flag on the reader.
    ``raise_corrupt=True`` makes PdfReader(...) raise the same
    PdfReadError class we expose via the fake module — so that
    ``extract_pdf_text``'s ``except PdfReadError`` catches it.
    """
    pages = pages or ["page text"]

    class _FakePdfReadError(Exception):
        pass

    class _FakeReader:
        def __init__(self, _path):
            if raise_corrupt:
                raise _FakePdfReadError("simulated load failure")
            self.is_encrypted = encrypted
            self.pages = [SimpleNamespace(extract_text=lambda t=p: t) for p in pages]

        def decrypt(self, _password):
            return 0  # simulate "could not decrypt"

    fake_pypdf = SimpleNamespace(
        PdfReader=_FakeReader,
        errors=SimpleNamespace(PdfReadError=_FakePdfReadError),
    )
    fake_errors = SimpleNamespace(PdfReadError=_FakePdfReadError)

    monkeypatch.setitem(sys.modules, "pypdf", fake_pypdf)
    monkeypatch.setitem(sys.modules, "pypdf.errors", fake_errors)
    return _FakePdfReadError


def _write_fake_pdf(path: Path, content: bytes = b"%PDF-1.4 fake-content") -> None:
    path.write_bytes(content)


# ---------------------------------------------------------------- happy path
def test_extract_simple_pdf_returns_text(tmp_path, monkeypatch):
    pdf = tmp_path / "doc.pdf"
    _write_fake_pdf(pdf)
    _install_fake_pypdf(monkeypatch, pages=["Body of the paper. " * 20])

    text, err = extract_pdf_text(pdf)
    assert err is None
    assert "Body of the paper" in text


def test_extract_concatenates_multiple_pages(tmp_path, monkeypatch):
    pdf = tmp_path / "doc.pdf"
    _write_fake_pdf(pdf)
    _install_fake_pypdf(
        monkeypatch,
        pages=["Page one body. " * 20, "Page two body. " * 20],
    )

    text, err = extract_pdf_text(pdf)
    assert err is None
    assert "Page one body" in text
    assert "Page two body" in text


# ---------------------------------------------------------------- caching
def test_extract_caches_to_disk(tmp_path, monkeypatch):
    pdf = tmp_path / "doc.pdf"
    _write_fake_pdf(pdf)
    cache = tmp_path / "cache"

    _install_fake_pypdf(monkeypatch, pages=["Body content. " * 30])
    text1, _ = extract_pdf_text(pdf, cache_dir=cache)

    # After first call, the cache should hold one .txt file
    files = list(cache.glob("*.txt"))
    assert len(files) == 1
    assert "Body content" in files[0].read_text(encoding="utf-8")

    # Second call should hit the cache — install a different fake to prove
    # we don't re-run pypdf
    _install_fake_pypdf(monkeypatch, pages=["different content this time"])
    text2, _ = extract_pdf_text(pdf, cache_dir=cache)
    assert text2 == text1  # cache wins


def test_extract_cache_keyed_by_content_hash(tmp_path, monkeypatch):
    """Two PDFs with identical bytes share the same cache entry."""
    pdf_a = tmp_path / "a.pdf"
    pdf_b = tmp_path / "b.pdf"
    same_bytes = b"%PDF-1.4 deterministic-content"
    _write_fake_pdf(pdf_a, same_bytes)
    _write_fake_pdf(pdf_b, same_bytes)
    cache = tmp_path / "cache"

    _install_fake_pypdf(monkeypatch, pages=["shared content. " * 30])
    extract_pdf_text(pdf_a, cache_dir=cache)
    files_after_a = list(cache.glob("*.txt"))

    # Different fake — but content hash is same, so cache hit must use the
    # earlier extraction not re-run on b.
    _install_fake_pypdf(monkeypatch, pages=["should-not-appear"])
    text_b, _ = extract_pdf_text(pdf_b, cache_dir=cache)
    files_after_b = list(cache.glob("*.txt"))

    assert len(files_after_a) == len(files_after_b) == 1  # no new entry
    assert "should-not-appear" not in text_b


def test_content_hash_stable(tmp_path):
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"some bytes")
    assert _content_hash(pdf) == _content_hash(pdf)
    pdf.write_bytes(b"different bytes")
    # Hashing again on same path must reflect new content
    h1 = _content_hash(pdf)
    pdf.write_bytes(b"different bytes")
    h2 = _content_hash(pdf)
    assert h1 == h2


# ---------------------------------------------------------------- error paths
def test_extract_missing_file_returns_io_error(tmp_path):
    text, err = extract_pdf_text(tmp_path / "does_not_exist.pdf")
    assert text is None
    assert err == "io_error"


def test_extract_encrypted_returns_error(tmp_path, monkeypatch):
    pdf = tmp_path / "doc.pdf"
    _write_fake_pdf(pdf)
    _install_fake_pypdf(monkeypatch, encrypted=True)

    text, err = extract_pdf_text(pdf)
    assert text is None
    assert err == "encrypted"


def test_extract_corrupt_returns_error(tmp_path, monkeypatch):
    pdf = tmp_path / "doc.pdf"
    _write_fake_pdf(pdf)
    _install_fake_pypdf(monkeypatch, raise_corrupt=True)

    text, err = extract_pdf_text(pdf)
    assert text is None
    assert err == "corrupt"


def test_extract_below_min_chars_returns_empty_extraction(tmp_path, monkeypatch):
    pdf = tmp_path / "doc.pdf"
    _write_fake_pdf(pdf)
    _install_fake_pypdf(monkeypatch, pages=[""])  # scanned-image-style

    text, err = extract_pdf_text(pdf)
    assert text is None
    assert err == "empty_extraction"


def test_extract_short_text_below_threshold(tmp_path, monkeypatch):
    pdf = tmp_path / "doc.pdf"
    _write_fake_pdf(pdf)
    _install_fake_pypdf(monkeypatch, pages=["Tiny."])  # < 200 chars

    text, err = extract_pdf_text(pdf, min_chars=200)
    assert text is None
    assert err == "empty_extraction"


def test_extract_respects_min_chars_override(tmp_path, monkeypatch):
    """A lower min_chars threshold lets short PDFs through (e.g. one-pagers)."""
    pdf = tmp_path / "doc.pdf"
    _write_fake_pdf(pdf)
    _install_fake_pypdf(monkeypatch, pages=["Short letter body."])

    text, err = extract_pdf_text(pdf, min_chars=10)
    assert err is None
    assert "Short letter body" in text


def test_extract_pypdf_missing_returns_io_error(tmp_path, monkeypatch):
    """If pypdf can't be imported (yanked from env), surface as io_error."""
    pdf = tmp_path / "doc.pdf"
    _write_fake_pdf(pdf)
    monkeypatch.setitem(sys.modules, "pypdf", None)  # ImportError on `from pypdf import …`

    text, err = extract_pdf_text(pdf)
    assert text is None
    assert err == "io_error"
