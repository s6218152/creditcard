import os

from storage_utils import prune_old_files


def test_retention_removes_only_matching_old_files(tmp_path):
    old_pdf = tmp_path / "old.pdf"
    old_text = tmp_path / "old.txt"
    recent_pdf = tmp_path / "recent.pdf"
    for path in (old_pdf, old_text, recent_pdf):
        path.write_text("data")
    os.utime(old_pdf, (1, 1))
    os.utime(old_text, (1, 1))

    removed = prune_old_files(tmp_path, ("*.pdf",), 30, now=40 * 86400)

    assert removed == [old_pdf.resolve()]
    assert not old_pdf.exists()
    assert old_text.exists() and recent_pdf.exists()


def test_zero_retention_disables_cleanup(tmp_path):
    target = tmp_path / "old.pdf"
    target.write_text("data")
    os.utime(target, (1, 1))
    assert prune_old_files(tmp_path, ("*.pdf",), 0, now=40 * 86400) == []
    assert target.exists()
