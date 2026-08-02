from daylog import terminal
from daylog.config import Config, StorageConfig, TerminalConfig


def test_tail_baselines_on_first_sight_without_importing_history(tmp_path, conn):
    hist = tmp_path / "ConsoleHost_history.txt"
    hist.write_text("old-command-1\nold-command-2\n", encoding="utf-8")
    cfg = Config(storage=StorageConfig(data_dir=tmp_path),
                 terminal=TerminalConfig(enabled=True, powershell_history=hist))

    assert terminal.tail(conn, cfg) == 0
    rows = conn.execute("SELECT * FROM terminal_cmds").fetchall()
    assert rows == []


def test_tail_captures_only_newly_appended_commands(tmp_path, conn):
    hist = tmp_path / "ConsoleHost_history.txt"
    hist.write_text("old-command-1\nold-command-2\n", encoding="utf-8")
    cfg = Config(storage=StorageConfig(data_dir=tmp_path),
                 terminal=TerminalConfig(enabled=True, powershell_history=hist))
    terminal.tail(conn, cfg)  # baseline

    with open(hist, "a", encoding="utf-8") as fh:
        fh.write("cloudflared tunnel login\ncloudflared tunnel create valuelyne\n")
    n = terminal.tail(conn, cfg)

    assert n == 2
    cmds = [r["command"] for r in conn.execute("SELECT command FROM terminal_cmds ORDER BY id")]
    assert any("tunnel create" in c for c in cmds)


def test_tail_is_idempotent_on_repeat_calls(tmp_path, conn):
    hist = tmp_path / "ConsoleHost_history.txt"
    hist.write_text("old-command-1\n", encoding="utf-8")
    cfg = Config(storage=StorageConfig(data_dir=tmp_path),
                 terminal=TerminalConfig(enabled=True, powershell_history=hist))
    terminal.tail(conn, cfg)
    with open(hist, "a", encoding="utf-8") as fh:
        fh.write("new-command\n")
    terminal.tail(conn, cfg)
    assert terminal.tail(conn, cfg) == 0


def test_tail_disabled_returns_zero(tmp_path, conn):
    hist = tmp_path / "ConsoleHost_history.txt"
    hist.write_text("old-command-1\n", encoding="utf-8")
    cfg = Config(storage=StorageConfig(data_dir=tmp_path),
                 terminal=TerminalConfig(enabled=False, powershell_history=hist))
    assert terminal.tail(conn, cfg) == 0
    assert conn.execute("SELECT * FROM terminal_cmds").fetchall() == []


def test_parse_zsh_line_extracts_epoch_and_command():
    ts, command = terminal._parse_zsh_line(": 1700000000:0;git status")
    assert command == "git status"
    assert ts is not None


def test_parse_zsh_line_plain_line_has_no_timestamp():
    ts, command = terminal._parse_zsh_line("git status")
    assert ts is None
    assert command == "git status"
