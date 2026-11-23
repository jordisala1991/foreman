"""An HTTP or mail client imported outside a provider adapter fails `make lint`: the real target, run in a temporary tree."""

from pathlib import Path

import pytest
from helpers import lint


@pytest.mark.parametrize(
    "source",
    [
        "import urllib.request\n\nprint(urllib.request.urlopen)\n",
        "from urllib import request\n\nprint(request)\n",
        "from urllib.request import urlopen\n\nprint(urlopen)\n",
        "import http.client\n\nprint(http.client.HTTPSConnection)\n",
        "from http import client\n\nprint(client)\n",
        "import ssl\n\nprint(ssl)\n",
        "import ftplib\n\nprint(ftplib)\n",
        "import smtplib\n\nprint(smtplib)\n",
        "import imaplib\n\nprint(imaplib)\n",
        "import poplib\n\nprint(poplib)\n",
        "import telnetlib\n\nprint(telnetlib)\n",
    ],
)
def test_a_client_of_the_platform_fails_lint(tmp_path: Path, source: str) -> None:
    result = lint(tmp_path, source)
    assert result.returncode != 0
    assert "TID251" in result.stdout


@pytest.mark.parametrize(
    "source",
    [
        'from urllib.parse import quote\n\nprint(quote("a b"))\n',
        "import http\n\nprint(http.HTTPStatus.OK)\n",
        "import socket\n\nprint(socket.gethostname())\n",
    ],
)
def test_a_module_that_uses_no_client_passes_lint(tmp_path: Path, source: str) -> None:
    assert lint(tmp_path, source).returncode == 0
