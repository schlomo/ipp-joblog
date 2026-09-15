"""Allow ``python -m ipp_joblog``, which is how the container image runs."""

from ipp_joblog.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
