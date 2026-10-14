"""Shell completion works for bash, zsh, fish and PowerShell (spec W7 item 5).

Root cause this guards: completion came only from Typer's defaults and nothing in
the repository exercised it, so a change that broke it (a lazily built group that
raised during resolution, an option callback that ran at completion time, a
renamed entry point) would ship unnoticed. These tests run the installed
``beakr`` entry point the way a shell does:

1. ``beakr --show-completion <shell>`` prints a script for each shell, and the
   script parses (``bash -n``, ``zsh -n``, ``fish -n``, PowerShell's parser) when
   that shell is on PATH.
2. The completion protocol (``_BEAKR_COMPLETE=complete_<shell>`` plus the
   shell's argument variables) offers ``kb`` and ``keys`` for ``beakr k``.
3. Where the shell exists, the generated script is sourced and its completion
   function is called, so the shell-side half of the protocol is covered too.

A shell missing from this machine skips its shell-side checks with the reason,
unless ``BEAKR_REQUIRE_SHELLS`` names it (CI sets it for the shells it installs);
the protocol tests need no shell and always run.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

#: The `beakr` console script installed into the same environment as this
#: interpreter (``uv sync`` installs the project, so it is always present).
BIN_DIR = Path(sys.executable).parent
BEAKR = BIN_DIR / ("beakr.exe" if os.name == "nt" else "beakr")

SHELLS = ("bash", "zsh", "fish", "powershell", "pwsh")


@pytest.fixture
def env(tmp_path) -> dict[str, str]:
    """A clean environment: no real config or keychain, `beakr` first on PATH."""
    base = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("BEAKR_", "_BEAKR_", "_TYPER_", "COMP_"))
    }
    base.update(
        HOME=str(tmp_path),
        XDG_CONFIG_HOME=str(tmp_path / ".config"),
        PATH=f"{BIN_DIR}{os.pathsep}{os.environ.get('PATH', '')}",
        # Lets `--show-completion` take the shell as its value instead of
        # detecting the parent shell, which under pytest is not an interactive shell.
        _TYPER_COMPLETE_TEST_DISABLE_SHELL_DETECTION="1",
        NO_COLOR="1",
    )
    return base


def _run(args: list[str], env: dict[str, str], **extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        env={**env, **extra},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _script(shell: str, env: dict[str, str], tmp_path: Path) -> Path:
    result = _run([str(BEAKR), "--show-completion", shell], env)
    assert result.returncode == 0, result.stderr
    path = tmp_path / f"beakr-completion.{shell}"
    path.write_text(result.stdout)
    return path


#: Shells CI installs on purpose (comma-separated, e.g. ``bash,zsh,fish``): for
#: these a missing binary fails instead of skipping, so a broken CI image cannot
#: silently turn the shell-side checks off.
REQUIRED_SHELLS = {
    name.strip() for name in os.environ.get("BEAKR_REQUIRE_SHELLS", "").split(",") if name.strip()
}


def _require(*names: str) -> str:
    for name in names:
        found = shutil.which(name)
        if found is not None:
            return found
    missing = " or ".join(names)
    if REQUIRED_SHELLS.intersection(names):
        pytest.fail(f"{missing} is required (BEAKR_REQUIRE_SHELLS) but not installed")
    pytest.skip(f"{missing} is not installed on this machine")


def test_the_beakr_entry_point_is_installed():
    assert BEAKR.exists(), f"no beakr console script at {BEAKR}; run `uv sync`"


def test_help_offers_completion_install_and_show():
    from typer.testing import CliRunner

    from beakr_cli.main import app

    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    assert "--install-completion" in result.stdout
    assert "--show-completion" in result.stdout


# ---------------------------------------------------------------------------
# Generated scripts
# ---------------------------------------------------------------------------

#: A fragment each shell's script must contain: it registers a completer for
#: `beakr` and calls back into `beakr` through the `_BEAKR_COMPLETE` variable.
REGISTRATION = {
    "bash": "complete -o default -F _beakr_completion beakr",
    "zsh": "compdef _beakr_completion beakr",
    "fish": "complete --command beakr",
    "powershell": "Register-ArgumentCompleter -Native -CommandName beakr",
    "pwsh": "Register-ArgumentCompleter -Native -CommandName beakr",
}

#: How each script asks `beakr` for matches: the `_BEAKR_COMPLETE` instruction.
CALLBACK = {
    "bash": "_BEAKR_COMPLETE=complete_bash",
    "zsh": "_BEAKR_COMPLETE=complete_zsh",
    "fish": "_BEAKR_COMPLETE=complete_fish",
    "powershell": '$Env:_BEAKR_COMPLETE = "complete_powershell"',
    "pwsh": '$Env:_BEAKR_COMPLETE = "complete_powershell"',
}


@pytest.mark.parametrize("shell", SHELLS)
def test_show_completion_prints_a_script_for_each_shell(shell, env, tmp_path):
    text = _script(shell, env, tmp_path).read_text()
    assert REGISTRATION[shell] in text
    assert CALLBACK[shell] in text


def test_an_unknown_shell_is_refused(env):
    result = _run([str(BEAKR), "--show-completion", "tcsh"], env)
    assert result.returncode != 0


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_generated_script_parses_in_its_shell(shell, env, tmp_path):
    binary = _require(shell)
    script = _script(shell, env, tmp_path)
    result = _run([binary, "-n", str(script)], env)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
def test_generated_powershell_script_parses(shell, env, tmp_path):
    binary = _require("pwsh", "powershell")
    script = _script(shell, env, tmp_path)
    parse = (
        "$errors = $null; "
        "[System.Management.Automation.Language.Parser]::ParseFile("
        f"'{script}', [ref]$null, [ref]$errors) | Out-Null; "
        "if ($errors) { $errors | ForEach-Object { Write-Error $_ }; exit 1 }"
    )
    result = _run([binary, "-NoProfile", "-NonInteractive", "-Command", parse], env)
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# The completion protocol (no shell needed)
# ---------------------------------------------------------------------------


def _complete(shell: str, words: str, env: dict[str, str]) -> str:
    """Ask `beakr` for completions the way `shell`'s generated script does."""
    extra = {"_BEAKR_COMPLETE": f"complete_{shell}"}
    if shell == "bash":
        parts = words.split(" ")
        extra.update(COMP_WORDS=words, COMP_CWORD=str(len(parts) - 1))
    elif shell == "fish":
        extra.update(_TYPER_COMPLETE_ARGS=words, _TYPER_COMPLETE_FISH_ACTION="get-args")
    elif shell == "powershell":
        extra.update(
            _TYPER_COMPLETE_ARGS=words,
            _TYPER_COMPLETE_WORD_TO_COMPLETE=words.rsplit(" ", 1)[-1],
        )
    else:
        extra.update(_TYPER_COMPLETE_ARGS=words)
    result = _run([str(BEAKR)], env, **extra)
    assert result.returncode == 0, result.stderr
    return result.stdout


def _offered(shell: str, output: str) -> set[str]:
    if shell == "zsh":
        # _arguments '*: :(("kb":"help"\n"keys":"help"))'
        return {line.split('"')[1] for line in output.splitlines() if line.count('"') >= 2}
    if shell == "fish":
        return {line.split("\t")[0] for line in output.splitlines() if line}
    if shell == "powershell":
        return {line.split(":::")[0] for line in output.splitlines() if line}
    return {line for line in output.splitlines() if line}


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish", "powershell"])
def test_protocol_completes_top_level_commands(shell, env):
    offered = _offered(shell, _complete(shell, "beakr k", env))
    assert {"kb", "keys"} <= offered
    assert all(word.startswith("k") for word in offered), offered


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish", "powershell"])
def test_protocol_completes_subcommands_and_hides_deprecated_aliases(shell, env):
    # `kb proposals list` is a hidden alias of `ls` (tests/test_cli_command_grammar.py);
    # completion offers only the spec verb.
    offered = _offered(shell, _complete(shell, "beakr kb proposals l", env))
    assert offered == {"ls"}


def test_protocol_completes_the_lazily_built_api_group(env):
    # `beakr api` is resolved lazily by BeakrGroup.get_command; completion must
    # still find it and walk into its generated areas.
    assert "api" in _offered("bash", _complete("bash", "beakr ap", env))
    assert _offered("bash", _complete("bash", "beakr api ", env))


def test_protocol_completes_options(env):
    offered = _offered("bash", _complete("bash", "beakr kb ls --j", env))
    assert "--json" in offered


# ---------------------------------------------------------------------------
# The shell-side half: source the script and call its completer
# ---------------------------------------------------------------------------


def test_bash_script_completes_when_sourced(env, tmp_path):
    bash = _require("bash")
    script = _script("bash", env, tmp_path)
    # bash 3.2 (macOS) cannot `source <(...)`, so the script is read from a file.
    program = (
        f"source '{script}'; COMP_WORDS=(beakr k); COMP_CWORD=1; "
        '_beakr_completion beakr; printf "%s\\n" "${COMPREPLY[@]}"'
    )
    result = _run([bash, "--noprofile", "--norc", "-c", program], env)
    assert result.returncode == 0, result.stderr
    assert {"kb", "keys"} <= set(result.stdout.split())


def test_zsh_script_completes_when_sourced(env, tmp_path):
    zsh = _require("zsh")
    script = _script("zsh", env, tmp_path)
    # Outside an interactive completion widget `compdef` and `_arguments` do not
    # exist; stubs record what the script registers and the matches it hands zsh.
    program = (
        "compdef() { print -r -- registered:$1 }; "
        "_arguments() { print -rl -- $@ }; "
        f"source '{script}'; words=(beakr k); CURRENT=2; _beakr_completion"
    )
    result = _run([zsh, "-f", "-c", program], env)
    assert result.returncode == 0, result.stderr
    assert "registered:_beakr_completion" in result.stdout
    assert '"kb"' in result.stdout and '"keys"' in result.stdout


def test_fish_script_completes_when_sourced(env, tmp_path):
    fish = _require("fish")
    script = _script("fish", env, tmp_path)
    program = f"source '{script}'; complete -C 'beakr k'"
    result = _run([fish, "--no-config", "-c", program], env)
    assert result.returncode == 0, result.stderr
    offered = {line.split("\t")[0] for line in result.stdout.splitlines() if line}
    assert {"kb", "keys"} <= offered
