#!/bin/sh
# Run from any directory; install requirements-build.txt in your build environment first.
set -eu
cd -- "$(dirname -- "$0")/.."
exec "${MINDTRAIL_BUILD_PYTHON:-python3}" src/installer_build.py mac "$@"
