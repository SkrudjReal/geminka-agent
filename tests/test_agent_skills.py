from types import SimpleNamespace

from app.services import bot_api_mcp as bridge


def configure(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "_SKILLS_ROOT", tmp_path / "agent_skills")
    monkeypatch.setattr(bridge, "_requester_id", lambda: 42)
    monkeypatch.setattr(bridge.config, "settings", SimpleNamespace(is_user_allowed=lambda uid: uid == 42))
    directory = bridge._SKILLS_ROOT / "geminka"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text('---\nname: geminka\ndescription: "Capabilities"\n---\nProcedure')
    return directory


async def test_discovery_and_read_are_live_without_execution(tmp_path, monkeypatch):
    directory = configure(tmp_path, monkeypatch)
    result = await bridge.skill_list()
    assert result == {"ok": True, "skills": [{"name": "geminka", "description": "Capabilities"}]}
    assert (await bridge.skill_read("geminka"))["content"].endswith("Procedure")
    references = directory / "references"
    references.mkdir()
    reference = references / "tools.md"
    reference.write_text("Original")
    assert (await bridge.skill_read("geminka", "references/tools.md"))["content"] == "Original"
    reference.write_text("Updated")
    assert (await bridge.skill_read("geminka", "references/tools.md"))["content"] == "Updated"
    second = bridge._SKILLS_ROOT / "new-skill"
    second.mkdir()
    (second / "SKILL.md").write_text("# New")
    assert len((await bridge.skill_list())["skills"]) == 2


async def test_skill_read_rejects_escape_symlinks_and_oversized_files(tmp_path, monkeypatch):
    directory = configure(tmp_path, monkeypatch)
    outside = tmp_path / "outside.md"
    outside.write_text("private")
    (directory / "link.md").symlink_to(outside)
    (directory / "script.py").write_text("raise RuntimeError('must not execute')")
    (directory / "huge.md").write_text("x" * 65537)
    for name, document in [("../geminka", "SKILL.md"), ("geminka", "../../outside.md"),
                           ("geminka", str(outside)), ("geminka", "link.md"),
                           ("geminka", "script.py"), ("geminka", "huge.md")]:
        assert not (await bridge.skill_read(name, document))["ok"]
    (bridge._SKILLS_ROOT / "leaked").symlink_to(directory, target_is_directory=True)
    assert [item["name"] for item in (await bridge.skill_list())["skills"]] == ["geminka"]
    monkeypatch.setattr(bridge, "_requester_id", lambda: 99)
    assert not (await bridge.skill_read("geminka"))["ok"]
    assert not (await bridge.skill_list())["ok"]
