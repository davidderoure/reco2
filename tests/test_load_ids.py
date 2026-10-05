"""Tests for trial.client.load_ids."""

import os
import pytest

from trial.client import load_ids


def _write(tmp_path, content):
    p = tmp_path / "ids.txt"
    p.write_text(content)
    return str(p)


def test_load_ids_basic(tmp_path):
    path = _write(tmp_path, "AB12-CD34\nEF56-GH78\n")
    assert load_ids(path) == ["AB12-CD34", "EF56-GH78"]


def test_load_ids_ignores_blank_lines_and_comments(tmp_path):
    path = _write(tmp_path, "# usability trial\n\nAB12-CD34\n\n# another comment\nEF56-GH78\n")
    assert load_ids(path) == ["AB12-CD34", "EF56-GH78"]


def test_load_ids_missing_file():
    with pytest.raises(FileNotFoundError, match="ID list file not found"):
        load_ids("/nonexistent/path/ids.txt")


def test_load_ids_invalid_format(tmp_path):
    path = _write(tmp_path, "AB12-CD34\nnot-valid\n")
    with pytest.raises(ValueError, match="Invalid OriginId"):
        load_ids(path)


def test_load_ids_empty_file(tmp_path):
    path = _write(tmp_path, "# just a comment\n\n")
    with pytest.raises(ValueError, match="no valid OriginIds"):
        load_ids(path)


def test_load_ids_strips_whitespace(tmp_path):
    path = _write(tmp_path, "  AB12-CD34  \n  EF56-GH78  \n")
    assert load_ids(path) == ["AB12-CD34", "EF56-GH78"]
