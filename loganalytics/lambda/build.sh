#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
cd "$here"
rm -rf build
uv pip install --python 3.12 -r requirements.txt --target build --quiet
cp src/*.py build/
echo "built -> $here/build"
