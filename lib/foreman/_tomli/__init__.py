# Vendored from tomli 2.4.1 (https://github.com/hukkin/tomli, sdist https://pypi.org/project/tomli/2.4.1/, sha256
# 7c7e1a961a0b2f2472c1ac5b69affa0ae1132c39adcb67aba98568702b9cc23f), MIT licence in LICENSE next to this file.
# Unmodified below this header; foreman.toml_reader uses it only on Python < 3.11 (tomllib is tomli in the stdlib).
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2021 Taneli Hukkinen
# Licensed to PSF under a Contributor Agreement.

__all__ = ("loads", "load", "TOMLDecodeError")
__version__ = "2.4.1"  # DO NOT EDIT THIS LINE MANUALLY. LET bump2version UTILITY DO IT

from ._parser import TOMLDecodeError, load, loads
