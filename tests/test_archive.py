"""Tests for DigestArchive — write/prune/resolve/load round-trips."""

from news_digest.digest.archive import DigestArchive
from news_digest.models import Digest, DigestDocument, Top5Entry


def _doc() -> DigestDocument:
    return DigestDocument(
        digest=Digest(headline="H", date="01.06.2026",
                      top5=[Top5Entry(title="A", url="http://a")], rubrics={}, rest=[]),
        total_unique=1, total_raw=1)


def test_write_then_load_roundtrip(tmp_path):
    archive = DigestArchive(str(tmp_path))
    path = archive.write(_doc())
    loaded = archive.load(path)
    assert loaded.digest.headline == "H"
    assert loaded.digest.top5[0].url == "http://a"


def test_snapshot_paths_sorted(tmp_path):
    (tmp_path / "digest-2026-06-01T10-00.json").write_text("{}", encoding="utf-8")
    (tmp_path / "digest-2026-06-02T10-00.json").write_text("{}", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("x", encoding="utf-8")
    paths = DigestArchive(str(tmp_path)).snapshot_paths()
    assert len(paths) == 2
    assert paths[0].endswith("2026-06-01T10-00.json")


def test_resolve_by_prefix(tmp_path):
    (tmp_path / "digest-2026-06-01T10-00.json").write_text("{}", encoding="utf-8")
    archive = DigestArchive(str(tmp_path))
    assert archive.resolve("2026-06-01").endswith("2026-06-01T10-00.json")
    assert archive.resolve("2099") is None
