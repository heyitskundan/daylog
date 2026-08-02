from pathlib import Path

from daylog.config import Config, ObsidianConfig, StorageConfig, load_config, save_config


def test_save_and_load_config_round_trips(tmp_path):
    cfg = Config(
        storage=StorageConfig(data_dir=tmp_path / "data", screenshot_retention_days=21),
        obsidian=ObsidianConfig(vault_path=Path("D:/Vault/AT"), auto_detect=False),
    )
    cfg.ai.provider = "lmstudio"
    cfg.capture.hotkey = "ctrl+shift+9"

    config_file = tmp_path / "config.toml"
    save_config(cfg, config_file)
    reloaded = load_config(config_file)

    assert reloaded.ai.provider == "lmstudio"
    assert reloaded.capture.hotkey == "ctrl+shift+9"
    assert reloaded.storage.screenshot_retention_days == 21
    assert str(reloaded.obsidian.vault_path).replace("\\", "/").endswith("Vault/AT")


def test_load_config_ignores_unknown_keys(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[storage]\ndata_dir = \"" + str(tmp_path / "data").replace("\\", "/") + "\"\n"
        "bogus_key = \"whatever\"\n",
        encoding="utf-8",
    )
    cfg = load_config(config_file)
    assert not hasattr(cfg.storage, "bogus_key")


def test_load_config_missing_file_uses_defaults(tmp_path):
    cfg = load_config(tmp_path / "does-not-exist.toml")
    assert cfg.ai.provider == "ollama"
    assert cfg.segment.idle_gap_seconds == 180


def test_db_path_and_screenshots_dir_derive_from_data_dir(tmp_path):
    cfg = Config(storage=StorageConfig(data_dir=tmp_path))
    assert cfg.db_path == tmp_path / "daylog.db"
    assert cfg.screenshots_dir == tmp_path / "screenshots"
