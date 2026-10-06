from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.config import ConfigurationError
from app.core.instance import acquire_bot_lock
from scripts.deploy_service import deploy, select_service, unit_text, unit_value


def test_service_names_collisions_and_reuse(tmp_path):
    units = tmp_path / "units"
    units.mkdir()
    first = tmp_path / "a" / "geminka-agent"
    second = tmp_path / "b" / "geminka-agent"
    assert select_service(first, units) == "geminka-agent.service"
    (units / "geminka-agent.service").write_text(unit_text(first))
    assert select_service(second, units) == "geminka-agent1.service"
    (units / "geminka-agent1.service").write_text(unit_text(second))
    assert select_service(second, units) == "geminka-agent1.service"
    assert select_service(tmp_path / "geminka-agent2", units) == "geminka-agent2.service"
    (units / "other.service").symlink_to(tmp_path / "absent")
    assert select_service(tmp_path / "other", units) == "other1.service"
    assert "%%" in unit_value('/path with "quotes"/%name')
    with pytest.raises(ValueError):
        unit_value("bad\npath")


def test_migration_only_stops_own_legacy_service(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    units = tmp_path / "units"
    units.mkdir()
    legacy = units / "geminka.service"
    legacy.write_text(f"WorkingDirectory={project}\nExecStart={project}/scripts/start.sh\n")
    with patch("scripts.deploy_service.subprocess.run") as run:
        run.return_value.stdout = "loaded\n"
        assert deploy(project, units) == "project.service"
        calls = [call.args[0] for call in run.call_args_list]
        assert ["systemctl", "--user", "disable", "--now", "geminka.service"] in calls
    legacy.write_text("WorkingDirectory=/another/project\nExecStart=/another/project/scripts/start.sh\n")
    with patch("scripts.deploy_service.subprocess.run") as run:
        run.return_value.stdout = "loaded\n"
        assert deploy(project, units) == "project.service"
        assert not any("disable" in call.args[0] for call in run.call_args_list)


def test_token_lock_is_shared_and_released(tmp_path):
    with acquire_bot_lock("123:fake", tmp_path):
        with pytest.raises(ConfigurationError, match="уже запущен"):
            acquire_bot_lock("123:rotated", tmp_path)
        with acquire_bot_lock("456:other", tmp_path):
            pass
    with acquire_bot_lock("123:fake", tmp_path):
        pass


def test_invalid_unit_does_not_stop_existing_bot(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    units = tmp_path / "units"
    with patch("scripts.deploy_service.subprocess.run") as run:
        run.return_value.stdout = "bad-setting\n"
        with pytest.raises(RuntimeError, match="existing service was not stopped"):
            deploy(project, units)
        assert not any("restart" in call.args[0] or "disable" in call.args[0] for call in run.call_args_list)


def test_runner_never_kills_unrelated_processes():
    runner = (Path(__file__).resolve().parents[1] / "run.sh").read_text()
    assert "pgrep" not in runner
    assert "kill -15" not in runner


def test_parallel_agy_runtime_isolation(monkeypatch, tmp_path):
    import shutil
    import subprocess
    import sys

    from app.services.sandbox import agy_sandbox_command

    if not shutil.which("bwrap"):
        pytest.skip("Bubblewrap is not installed")
    # Use a fake auth home: this test never reads or copies real credentials.
    home = tmp_path / "home"
    source = home / ".gemini" / "antigravity-cli"
    source.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    processes = []
    roots = [tmp_path / "first", tmp_path / "second"]
    for index, root in enumerate(roots):
        root.mkdir()
        code = f"from pathlib import Path; Path({str(source / 'test-state')!r}).write_text({str(index)!r})"
        processes.append(subprocess.Popen(
            agy_sandbox_command([sys.executable, "-c", code], root, restricted=not index),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ))
    for process in processes:
        _, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, stderr.decode()
    assert not (source / "test-state").exists()
    assert [(root / "data/agy_sandbox/test-state").read_text() for root in roots] == ["0", "1"]
