"""Only provider adapters talk to the platform. ruff bans the Python HTTP clients; a network program run through proc
(curl, wget, ssh...) would reach the platform past them, and no foreman/ module names one today, so none may. A module
that names one of these programs is one that runs it."""

import pytest
from helpers import PLUGIN, names_program

NETWORK = {"curl", "wget", "nc", "ncat", "ssh", "scp", "sftp", "ftp", "telnet", "socat"}


def names_network_program(source: str) -> bool:
    return names_program(source, *NETWORK)


def test_no_module_names_a_network_program():
    root = PLUGIN / "foreman"
    found = {f.relative_to(root).as_posix() for f in root.rglob("*.py") if names_network_program(f.read_text())}
    assert found == set()


@pytest.mark.parametrize("program", sorted(NETWORK))
def test_a_network_program_is_found(program: str):
    assert names_network_program(f'proc.run(["{program}", "-X", "PUT", "https://x"])\n')


@pytest.mark.parametrize("program", sorted(NETWORK))
def test_a_program_given_by_its_path_is_found(program: str):
    assert names_network_program(f'proc.run(["/usr/bin/{program}", "x"])\n')


def test_a_module_without_one_is_not_found():
    assert not names_network_program('proc.run(["git", "status"])\n')


def test_a_longer_name_is_not_found():
    assert not names_network_program('x = "curly"\ny = "ncurses"\nz = "sshd"\n')


def test_a_path_to_another_name_is_not_found():
    assert not names_network_program('x = "/usr/bin/curly"\ny = "/usr/bin/curl/"\n')
