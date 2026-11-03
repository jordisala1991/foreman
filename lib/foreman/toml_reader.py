"""The one TOML reader of foreman (config.py and the tests import it, never tomllib or tomli directly).

Python 3.11+ has tomllib in the standard library; on 3.9 and 3.10 foreman uses its own copy of tomli (_tomli/, the
same parser tomllib was made from, MIT, provenance in its files), so foreman needs nothing from pip.
"""

try:
    from tomllib import TOMLDecodeError, loads
except ImportError:  # Python < 3.11
    from ._tomli import TOMLDecodeError, loads

__all__ = ("TOMLDecodeError", "loads")
