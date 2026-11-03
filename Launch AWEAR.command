#!/bin/sh
# Source checkout shortcut. Distributed .app builds already include Python.
cd -- "$(dirname -- "$0")" || exit 1
if [ -x .venv-desktop/bin/python ]; then
  exec .venv-desktop/bin/python scripts/run-local.py desktop
fi
printf '%s\n' 'Desktop dependencies are missing. Follow README → Desktop launcher to set up this source checkout.'
printf '%s' 'Press Return to close. '
read -r answer
