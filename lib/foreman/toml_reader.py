"""The one TOML reader of foreman (config.py and the tests import it, never tomllib or tomli directly).

Python 3.11+ has tomllib in the standard library; on 3.9 and 3.10 foreman uses its own copy of tomli (_tomli/, the
same parser tomllib was made from, MIT, provenance in its files), so foreman needs nothing from pip.
"""

import sys

# A version test, not try/except ImportError: pyright, set to 3.9, then skips the tomllib branch instead of looking
# for the module in whichever Python it finds on the machine (found on a newer one, missing on a 3.9).
if sys.version_info >= (3, 11):
    from tomllib import TOMLDecodeError, loads
else:
    from ._tomli import TOMLDecodeError, loads

__all__ = ("TOMLDecodeError", "loads")
