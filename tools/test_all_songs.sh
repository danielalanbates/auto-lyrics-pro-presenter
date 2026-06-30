#!/bin/bash
set -e

for file in tests/playlist/*.txt; do
    echo "========================================"
    echo "Testing $file..."
    echo "========================================"
    python tools/make_test_song.py "$file"
    python tools/e2e_live.py
done
echo "All 10 songs passed perfectly!"
