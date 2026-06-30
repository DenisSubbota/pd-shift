from pd_shift.settings import load_config


def test_load_config_from_conf(tmp_path, monkeypatch):
    conf = tmp_path / "conf"
    conf.write_text(
        "\n".join(
            [
                "# sample",
                "token=abc123",
                "team_id=PTEAM01",
                "from_email=user@example.test",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("pd_shift.settings.CONFIG_FILE", conf)
    assert load_config() == {
        "token": "abc123",
        "team_id": "PTEAM01",
        "from_email": "user@example.test",
    }


def test_config_value_env_overrides_file(tmp_path, monkeypatch):
    conf = tmp_path / "conf"
    conf.write_text("token=from-file\n", encoding="utf-8")
    monkeypatch.setattr("pd_shift.settings.CONFIG_FILE", conf)
    monkeypatch.setenv("PD_TOKEN", "from-env")
    from pd_shift.settings import config_value

    assert config_value("token", env_names=("PD_TOKEN",)) == "from-env"
