import importlib

from app import config


def test_data_dir_defaults_to_repo_data(monkeypatch):
    monkeypatch.delenv("AMAROTTS_DATA_DIR", raising=False)
    try:
        assert importlib.reload(config).DATA_DIR == config.ROOT.parent / "data"
    finally:
        importlib.reload(config)


def test_data_dir_can_be_overridden(monkeypatch, tmp_path):
    monkeypatch.setenv("AMAROTTS_DATA_DIR", str(tmp_path))
    try:
        cfg = importlib.reload(config)
        assert (cfg.DATA_DIR, cfg.DOCS_DIR, cfg.EXPORTS_DIR) == (tmp_path, tmp_path / "docs", tmp_path / "exports")
    finally:
        monkeypatch.delenv("AMAROTTS_DATA_DIR")
        importlib.reload(config)
