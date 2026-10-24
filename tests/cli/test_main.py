"""Tests for supernote.cli.main."""

import argparse
import importlib
import sys

import pytest

from supernote.cli.main import main


def test_main_help(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "argv", ["supernote", "--help"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "Supernote toolkit" in captured.out


def test_main_no_command(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "argv", ["supernote"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "Supernote toolkit" in captured.out


def test_main_dispatch_success(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "argv", ["supernote", "analyze", "--help"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "usage: supernote analyze" in captured.out


def test_main_missing_dependency_error(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    original_import = importlib.import_module

    def fake_import(name: str, package: str | None = None) -> object:
        if name in (".admin", ".client"):
            raise ImportError("No module named 'fake_dep'")
        return original_import(name, package=package)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    monkeypatch.setattr(sys, "argv", ["supernote", "admin"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 1

    captured = capsys.readouterr()
    assert "Command 'admin' failed to load due to missing dependencies" in captured.out
    assert "pip install 'supernote[client]'" in captured.out


def test_main_missing_dependency_error_non_admin(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    original_import = importlib.import_module

    def fake_import(name: str, package: str | None = None) -> object:
        if name == ".server":
            raise ModuleNotFoundError("No module named 'sqlalchemy'")
        return original_import(name, package=package)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    monkeypatch.setattr(sys, "argv", ["supernote", "server"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 1

    captured = capsys.readouterr()
    assert "Command 'server' failed to load due to missing dependencies" in captured.out
    assert "pip install 'supernote[server]'" in captured.out


def test_main_no_func_attr(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        argparse.ArgumentParser,
        "parse_args",
        lambda self: argparse.Namespace(command="custom"),
    )
    monkeypatch.setattr(sys, "argv", ["supernote"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 1
