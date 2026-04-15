from __future__ import annotations

from pathlib import Path

import ezsk.paths as paths_mod


def test_path_functions_return_paths(tmp_config_dir, tmp_data_dir, monkeypatch):
    """All public path functions return Path objects."""
    monkeypatch.setattr(paths_mod, "_migrated", True)  # skip migration
    assert isinstance(paths_mod.config_dir(), Path)
    assert isinstance(paths_mod.data_dir(), Path)
    assert isinstance(paths_mod.default_config_path(), Path)
    assert isinstance(paths_mod.default_state_path(), Path)
    assert isinstance(paths_mod.default_log_path(), Path)
    assert isinstance(paths_mod.default_dashboard_path(), Path)
    assert isinstance(paths_mod.default_downloads_dir(), Path)
    assert isinstance(paths_mod.default_slskd_data_dir(), Path)


def test_config_path_ends_with_config_toml(tmp_config_dir, tmp_data_dir, monkeypatch):
    monkeypatch.setattr(paths_mod, "_migrated", True)
    assert paths_mod.default_config_path().name == "config.toml"


def test_state_path_ends_with_state_json(tmp_config_dir, tmp_data_dir, monkeypatch):
    monkeypatch.setattr(paths_mod, "_migrated", True)
    assert paths_mod.default_state_path().name == "state.json"


def test_migration_copies_legacy_config(tmp_path, monkeypatch):
    """Legacy config.toml is copied to new location if new doesn't exist."""
    legacy_config = tmp_path / "legacy_config" / "ezsk"
    legacy_config.mkdir(parents=True)
    (legacy_config / "config.toml").write_text("[slskd]\nurl = 'test'\n")

    new_config = tmp_path / "new_config"
    new_config.mkdir()

    monkeypatch.setattr(paths_mod, "_migrated", False)
    monkeypatch.setattr(paths_mod, "_LEGACY_CONFIG_DIR", legacy_config)
    monkeypatch.setattr(paths_mod, "_LEGACY_DATA_DIR", tmp_path / "nonexistent")
    monkeypatch.setattr(paths_mod, "user_config_dir", lambda *a, **kw: str(new_config))
    monkeypatch.setattr(paths_mod, "user_data_dir", lambda *a, **kw: str(tmp_path / "data"))

    paths_mod._migrate_if_needed()

    assert (new_config / "config.toml").exists()
    assert "[slskd]" in (new_config / "config.toml").read_text()


def test_migration_does_not_overwrite_existing(tmp_path, monkeypatch):
    """Migration doesn't overwrite if new config already exists."""
    legacy_config = tmp_path / "legacy_config" / "ezsk"
    legacy_config.mkdir(parents=True)
    (legacy_config / "config.toml").write_text("legacy content")

    new_config = tmp_path / "new_config"
    new_config.mkdir()
    (new_config / "config.toml").write_text("new content")

    monkeypatch.setattr(paths_mod, "_migrated", False)
    monkeypatch.setattr(paths_mod, "_LEGACY_CONFIG_DIR", legacy_config)
    monkeypatch.setattr(paths_mod, "_LEGACY_DATA_DIR", tmp_path / "nonexistent")
    monkeypatch.setattr(paths_mod, "user_config_dir", lambda *a, **kw: str(new_config))
    monkeypatch.setattr(paths_mod, "user_data_dir", lambda *a, **kw: str(tmp_path / "data"))

    paths_mod._migrate_if_needed()

    assert (new_config / "config.toml").read_text() == "new content"
