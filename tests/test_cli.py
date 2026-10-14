import json
import sys
from io import StringIO

import httpx
import pytest
from pydantic_settings import CliApp

from caching_service.cli import CliSettings, main


@pytest.fixture
def transport(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == "POST":
            assert json.loads(request.content) == {"list_1": ["a"], "list_2": ["b"]}
            return httpx.Response(200, json={"id": "a" * 64})
        assert request.url.path == "/payload/" + "a" * 64
        return httpx.Response(200, json={"output": "A, B"})

    original = httpx.Client
    monkeypatch.setattr(
        "caching_service.cli.httpx.Client",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    return calls


def test_inline_short_flags_repeat_stdout(transport, capsys):
    CliApp.run(
        CliSettings,
        cli_args=["-j", '{"list_1":["a"],"list_2":["b"]}', "-r", "3", "-o", "-"],
    )
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(records) == 3
    assert records == [{"id": "a" * 64, "output": "A, B"}] * 3
    assert len(transport) == 6


def test_file_and_stdin(transport, tmp_path, monkeypatch):
    source = tmp_path / "input.json"
    destination = tmp_path / "output.jsonl"
    source.write_text('{"list_1":["a"],"list_2":["b"]}', encoding="utf-8")
    CliApp.run(CliSettings, cli_args=["-i", str(source), "-o", str(destination)])
    assert json.loads(destination.read_text())["output"] == "A, B"
    monkeypatch.setattr(sys, "stdin", StringIO(source.read_text()))
    CliApp.run(CliSettings, cli_args=["--input", "-", "--output", str(destination)])
    assert len(transport) == 4


@pytest.mark.parametrize(
    "args",
    [
        ["--repeat", "0"],
        ["--repeat", "no"],
        ["--host", "ftp://example.com"],
        ["--host", "http://example.com/?secret=x"],
        ["--input", "-", "--json", "{}"],
        ["--json", "not json"],
        ["--json", '{"list_1":["a"],"list_2":[]}'],
    ],
)
def test_bad_input_exits_two(args, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["cache-cli", *args])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2


def test_help(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["cache-cli", "-h"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 0
    assert "--host" in capsys.readouterr().out


def test_http_error_exits_one(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("caching_service.cli.httpx.Client", fail)
    monkeypatch.setattr(sys, "argv", ["cache-cli", "-j", '{"list_1":["a"],"list_2":["b"]}'])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1
    assert "connection refused" in capsys.readouterr().err
