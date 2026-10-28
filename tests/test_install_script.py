"""``install.sh`` reaches ``beakr setup`` with and without a terminal.

Root cause guarded (sandbox e2e, 2026-10-27): the script chose interactive
setup with ``[ -e /dev/tty ]``, which is true even when the process has no
controlling terminal (an agent's or CI's shell). Redirecting from /dev/tty then
failed with "Device not configured" and, under ``set -e``, the script exited 1
before ``beakr setup`` ran, so the person got no sign-in and no MCP server. It
now tests that /dev/tty opens, and runs setup non-interactively when it does
not.

``uv``, ``git`` and ``beakr`` are stubs on PATH; each run records the
arguments ``beakr`` got and whether its stdin was a terminal.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name == "nt", reason="install.sh is a POSIX script")

SCRIPT = Path(__file__).resolve().parents[1] / "install.sh"
SHELLS = [shell for shell in ("sh", "dash", "bash") if shutil.which(shell)]

_BEAKR_STUB = """#!/bin/sh
if [ -t 0 ]; then where=tty; else where=notty; fi
printf '%s stdin=%s\\n' "$*" "$where" >> "$BEAKR_STUB_LOG"
"""
_UV_STUB = """#!/bin/sh
[ "$1" = "--version" ] && echo "uv 0.0.0-stub"
exit 0
"""


def _stubs(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in {"beakr": _BEAKR_STUB, "uv": _UV_STUB, "git": "#!/bin/sh\nexit 0\n"}.items():
        stub = bin_dir / name
        stub.write_text(body)
        stub.chmod(0o755)
    home = tmp_path / "script-home"
    home.mkdir()
    return {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(home),
        "BEAKR_STUB_LOG": str(tmp_path / "beakr.log"),
    }


@pytest.mark.parametrize("shell", SHELLS)
def test_without_a_terminal_setup_runs_non_interactively(tmp_path: Path, shell: str) -> None:
    env = _stubs(tmp_path)

    # A new session has no controlling terminal, though /dev/tty still exists:
    # an agent's or CI's shell, or `curl ... | sh` run by one.
    result = subprocess.run(
        [shell, "-s", "--", "--no-auth", "--client", "claude"],
        input=SCRIPT.read_bytes(),
        env=env,
        capture_output=True,
        start_new_session=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr.decode()
    assert Path(env["BEAKR_STUB_LOG"]).read_text() == (
        "setup --no-auth --client claude stdin=notty\n"
    )
    # The rest of the script still ran after setup: setup did not eat it from the pipe.
    assert b"All set." in result.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="needs a pseudo-terminal")
def test_with_a_terminal_setup_reads_it(tmp_path: Path) -> None:
    import pty

    env = _stubs(tmp_path)
    pid, fd = pty.fork()
    if pid == 0:  # the child: the pty is its controlling terminal
        os.execve("/bin/sh", ["sh", str(SCRIPT), "--no-auth"], env)
    output = b""
    while True:
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            break
        if not chunk:
            break
        output += chunk
    _, status = os.waitpid(pid, 0)

    assert os.waitstatus_to_exitcode(status) == 0, output.decode()
    assert Path(env["BEAKR_STUB_LOG"]).read_text() == "setup --no-auth stdin=tty\n"
