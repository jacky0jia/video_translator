from pathlib import Path

from app.api.fs import BrowseRequest, browse_filesystem


def test_file_browser_lists_directories_and_files(tmp_path: Path):
    (tmp_path / "Models").mkdir()
    (tmp_path / "voices.bin").write_bytes(b"voices")

    result = browse_filesystem(BrowseRequest(path=str(tmp_path), mode="file"))

    assert result["current_path"] == str(tmp_path.resolve())
    assert [(item["name"], item["is_directory"]) for item in result["entries"]] == [
        ("Models", True),
        ("voices.bin", False),
    ]


def test_folder_browser_omits_files(tmp_path: Path):
    (tmp_path / "Models").mkdir()
    (tmp_path / "voices.bin").write_bytes(b"voices")

    result = browse_filesystem(BrowseRequest(path=str(tmp_path), mode="folder"))

    assert [item["name"] for item in result["entries"]] == ["Models"]


def test_file_path_starts_browser_in_parent_folder(tmp_path: Path):
    selected = tmp_path / "model.onnx"
    selected.write_bytes(b"model")

    result = browse_filesystem(BrowseRequest(path=str(selected), mode="file"))

    assert result["current_path"] == str(tmp_path.resolve())


def test_missing_path_falls_back_to_nearest_existing_parent(tmp_path: Path):
    result = browse_filesystem(BrowseRequest(path=str(tmp_path / "missing" / "child"), mode="folder"))

    assert result["current_path"] == str(tmp_path.resolve())
