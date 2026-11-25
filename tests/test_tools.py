import asyncio
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import bot


@pytest.fixture(autouse=True)
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "ROOT", tmp_path)
    monkeypatch.setattr(bot, "TOOL_CALLS", 0)
    bot._TEXT_CACHE.clear()
    return tmp_path


@pytest.mark.parametrize("name", [
    ".env", ".env.local", "a.pem", "a.key", "a.p12", "a.pfx",
    "id_rsa.pub", "id_ed25519", ".npmrc", ".pypirc", ".netrc",
    "credentials-dev.json", "app_secrets.yaml",
])
def test_secrets(root, name):
    (root / name).write_text("sensitive")
    assert name not in bot._tree()
    assert bot._search("sensitive", "") == "一致なし"
    assert "sensitive" not in bot._read(name, 1, 150)


@pytest.mark.parametrize("name", [".env.example", ".env.sample", ".env.template"])
def test_templates(root, name):
    (root / name).write_text("example")
    assert name in bot._tree()
    assert "example" in bot._read(name, 1, 150)


def test_gitignore(root):
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".gitignore").write_text("ignored.py\n")
    (root / "ignored.py").write_text("hidden")
    (root / "ok.py").write_text("visible")
    subprocess.run(["git", "-C", str(root), "add", "ok.py"], check=True)
    assert "ok.py" in bot._tree()
    assert "ignored.py" not in bot._tree()
    assert bot._search("hidden", "*.py") == "一致なし"
    assert "hidden" not in bot._read("ignored.py", 1, 150)


def test_symlink_escape(root, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "code.py"
    outside.write_text("outside_value")
    (root / "link.py").symlink_to(outside)
    assert "link.py" not in bot._tree()
    assert bot._search("outside_value", "") == "一致なし"
    assert "outside_value" not in bot._read("link.py", 1, 150)


def test_ranges(root):
    (root / "code.py").write_text("\n".join(str(i) for i in range(300)))
    assert "(2-4行" in bot._read("code.py", "4", "2")
    assert "(1-150行" in bot._read("code.py", -1, 300)
    assert "300 行しか" in bot._read("code.py", 400, 500)
    (root / "empty").touch()
    assert "0 行しか" in bot._read("empty", 1, 150)


def test_timeout(root):
    (root / "code.py").write_text("a" * 100000 + "!")
    assert "簡単なパターン" in bot._search("(a+)+$", "")


def test_callback_and_limit(root):
    async def run():
        callback = AsyncMock()
        params = SimpleNamespace(result_callback=callback)
        for _ in range(4):
            await bot.read_file(params, "missing", "bad", None)
        await bot.list_files(params)
        assert callback.await_count == 5
        assert "もっと調べますか" in callback.call_args.args[0]
        bot.TOOL_CALLS = 0
        (root / "code.py").write_text("ok")
        await bot.read_file(params, "code.py", "bad", 1)
        assert "失敗" in callback.call_args.args[0]
        await bot.search_code(params, None)
        assert "失敗" in callback.call_args.args[0]
    asyncio.run(run())


def test_cache_and_size(root, monkeypatch):
    path = root / "code.py"
    path.write_text("old")
    assert bot._read_text(path) == "old"
    path.write_text("new text")
    assert bot._read_text(path) == "new text"
    monkeypatch.setattr(bot, "MAX_FILE_BYTES", 2)
    assert bot._read_text(path) is None


def test_list_and_search(root, monkeypatch):
    (root / "code.py").write_text("hello")
    (root / "notes.txt").write_text("notes")
    assert bot._tree("*.py") == "code.py"
    assert bot._search("HELLO", "*.py") == "code.py:1: hello"
    assert bot._search("[", "") == "一致なし"
    monkeypatch.setattr(bot, "MAX_TREE_FILES", 1)
    assert "ほか1ファイル" in bot._tree()


def test_missing_git(root, monkeypatch):
    def unavailable(*args, **kwargs):
        raise FileNotFoundError
    monkeypatch.setattr(bot.subprocess, "run", unavailable)
    (root / "code.py").touch()
    (root / "node_modules").mkdir()
    (root / "node_modules" / "hidden").touch()
    assert bot._tree() == "code.py"


def test_history():
    messages = [{"role": "system", "content": "system"}]
    for i in range(12):
        messages.extend([
            {"role": "user", "content": str(i)},
            {"role": "assistant", "tool_calls": [{"id": str(i)}]},
            {"role": "tool", "tool_call_id": str(i), "content": "x" * 500},
        ])
    bot._trim_history(messages)
    assert len(messages) == 31
    assert messages[1] == {"role": "user", "content": "2"}
    assert messages[2]["tool_calls"][0]["id"] == messages[3]["tool_call_id"]
    assert len(messages[3]["content"]) < 450
    bot._trim_history([])


def test_check_failure(capsys, monkeypatch):
    monkeypatch.setattr(bot, "_check_llm", AsyncMock(side_effect=ConnectionError))
    asyncio.run(bot.check())
    assert "APIキー・ネット接続" in capsys.readouterr().out
