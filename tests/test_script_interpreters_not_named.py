"""Only gitrepo.py runs git and only adapters talk to the platform. A script interpreter run through proc hides git, gh or
claude in a `-c`/`-e` string, which the adapter-only tests cannot see, so no foreman/ module names one. A module that names
one runs it; two name one and run nothing through proc: core/oversight/guard.py parses command lines (perl) and
services/launchd.py writes the interpreter into a launchd plist (python3). sys.executable is no string constant, so the
modules that run it are not named."""

import pytest
from helpers import PLUGIN, names_program

INTERPRETERS = {"python", "python3", "perl", "ruby", "node", "php", "lua", "osascript", "awk", "xargs"}
ALLOWED = {("core/oversight/guard.py", "perl"), ("services/launchd.py", "python3")}


def named_interpreters(source: str) -> set[str]:
    return {p for p in INTERPRETERS if names_program(source, p)}


def test_only_the_two_allowed_pairs_name_an_interpreter():
    root = PLUGIN / "foreman"
    found = {(f.relative_to(root).as_posix(), p) for f in root.rglob("*.py") for p in named_interpreters(f.read_text())}
    assert found == ALLOWED


@pytest.mark.parametrize("program", sorted(INTERPRETERS))
def test_an_interpreter_is_found(program: str):
    assert named_interpreters(f'proc.run(["{program}", "-c", "x"])\n') == {program}


@pytest.mark.parametrize("program", sorted(INTERPRETERS))
def test_an_interpreter_given_by_its_path_is_found(program: str):
    assert named_interpreters(f'proc.run(["/usr/bin/{program}", "x"])\n') == {program}


def test_a_module_without_one_is_not_found():
    assert named_interpreters('proc.run(["git", "status"])\n') == set()


def test_a_longer_name_is_not_found():
    assert named_interpreters('a = "pythonic"\nb = "perlin"\nc = "nodejs"\nd = "awkward"\n') == set()


def test_a_path_to_another_name_is_not_found():
    assert named_interpreters('a = "/usr/bin/pythonic"\nb = "/usr/bin/perl/"\n') == set()
