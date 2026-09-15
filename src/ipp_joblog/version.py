"""Where the version comes from, in the order it can be trusted.

The number itself is ``1.0.<number of commits on main>``, produced by the git
tag CI creates on every push. A container image carries no git history and no
installed package metadata, so it gets the value through the environment.
"""

from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as installed_version

FALLBACK = "0.0.0+unknown"


def resolve() -> str:
    from_env = os.environ.get("IPP_JOBLOG_VERSION")
    if from_env:
        return from_env
    try:  # written into the wheel at build time by hatch-vcs
        from ipp_joblog._version import __version__

        return __version__
    except ImportError:
        pass
    try:
        return installed_version("ipp-joblog")
    except PackageNotFoundError:
        return FALLBACK
