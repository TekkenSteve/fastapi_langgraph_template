"""Unit tests for the Monty sandbox backend (graphs/shared/monty_sandbox.py).

pydantic-monty is in the dev dependency group, so these run the real thing.
"""

import pytest

from shared.monty_sandbox import MontySandboxBackend, make_monty_backend


@pytest.fixture
def backend() -> MontySandboxBackend:
    return MontySandboxBackend(max_duration_secs=5.0)


def test_factory_constructs_backend() -> None:
    assert isinstance(make_monty_backend(), MontySandboxBackend)


def test_virtual_fs_round_trip(backend: MontySandboxBackend) -> None:
    backend.write("/skills/x/data.txt", "hello")
    read = backend.read("/skills/x/data.txt")
    assert read.error is None
    assert "hello" in read.file_data["content"]
    assert [e["path"] for e in backend.ls("/skills/x").entries] == ["/skills/x/data.txt"]
    assert backend.read("/nope.txt").error is not None


def test_grep_and_glob(backend: MontySandboxBackend) -> None:
    backend.write("/a.py", "import os\nprint(1)")
    backend.write("/b.txt", "nothing here")
    assert [m["path"] for m in backend.grep("import").matches] == ["/a.py"]
    assert [m["path"] for m in backend.glob("*.py").matches] == ["/a.py"]


def test_execute_inline_python(backend: MontySandboxBackend) -> None:
    result = backend.execute("python -c \"print('hi')\"")
    assert result.exit_code == 0
    assert result.output.strip() == "hi"


def test_execute_script_from_virtual_fs(backend: MontySandboxBackend) -> None:
    backend.write("/skills/coffee/ratio.py", "print(16 * 10)")
    result = backend.execute("python /skills/coffee/ratio.py")
    assert result.exit_code == 0
    assert result.output.strip() == "160"


def test_execute_unknown_command_rejected(backend: MontySandboxBackend) -> None:
    result = backend.execute("rm -rf /")
    assert result.exit_code == 2
    assert "Python only" in result.output


def test_execute_missing_script(backend: MontySandboxBackend) -> None:
    result = backend.execute("python /nope.py")
    assert result.exit_code == 2
    assert "not found" in result.output


def test_script_error_is_exit_1_with_traceback(backend: MontySandboxBackend) -> None:
    result = backend.execute("python -c \"raise ValueError('boom')\"")
    assert result.exit_code == 1
    assert "ValueError" in result.output


def test_network_is_denied(backend: MontySandboxBackend) -> None:
    result = backend.execute("python -c \"import urllib.request; urllib.request.urlopen('http://example.com')\"")
    assert result.exit_code == 1
    assert "urllib" in result.output or "ModuleNotFoundError" in result.output


def test_host_filesystem_is_denied(backend: MontySandboxBackend) -> None:
    result = backend.execute("python -c \"print(open('/etc/passwd').read())\"")
    assert result.exit_code == 1


def test_timeout_limit_kills_loops() -> None:
    backend = MontySandboxBackend(max_duration_secs=1.0)
    result = backend.execute("python -c 'while True: pass'")
    assert result.exit_code == 1
    assert "time limit" in result.output


def test_sibling_data_file_readable_via_mount(backend: MontySandboxBackend) -> None:
    backend.write("/skills/x/run.py", "print(open('/skills/x/data.txt').read())")
    backend.write("/skills/x/data.txt", "sibling-data")
    result = backend.execute("python /skills/x/run.py")
    assert result.exit_code == 0
    assert "sibling-data" in result.output
