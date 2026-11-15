#!/bin/sh
# Layout sweep of the macOS app (Aperture/Previews/LayoutSweep.swift): every screen at several
# window sizes, Chart inspector closed and open, in English and Russian, on the fixture data.
# Fails when the app crashes or hangs (for example AppKit's "Update Constraints in Window" loop
# while the window is resized). Needs a logged-in macOS session; no Screen Recording permission.
# Usage: apps/macos/scripts/layout-sweep.sh [output folder, default build/layout-sweep]
#   -> <folder>/<language>/<width>x<height>-<section>[-inspector].png
set -eu
cd "$(dirname "$0")/.."
OUT="${1:-build/layout-sweep}"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
# Seconds one language may take before the run counts as hung.
LIMIT=420
swift build --product ApertureMac
BIN="$(swift build --show-bin-path)/ApertureMac"
status=0
for lang in en ru; do
  rm -rf "${OUT:?}/$lang"
  mkdir -p "$OUT/$lang"
  # A separate defaults domain per run would be cleaner, but the swift-run binary has no bundle
  # id; the sweep resets the one key it changes (chart.decisions) when it ends.
  APERTURE_FIXTURES=1 APERTURE_LAYOUT_SWEEP="$OUT/$lang" \
    "$BIN" -AppleLanguages "($lang)" -AppleLocale "${lang}_US" -ApplePersistenceIgnoreState YES \
    -appearance dark > "$OUT/$lang.log" 2>&1 &
  pid=$!
  waited=0
  while kill -0 "$pid" 2>/dev/null && [ "$waited" -lt "$LIMIT" ]; do
    sleep 1
    waited=$((waited + 1))
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill -9 "$pid" || true
    echo "::error::layout sweep ($lang) hung for ${LIMIT}s"
    status=1
    continue
  fi
  if wait "$pid" && [ -f "$OUT/$lang/done" ]; then
    echo "layout sweep ($lang): $(ls "$OUT/$lang"/*.png | wc -l | tr -d ' ') pictures"
  else
    echo "::error::layout sweep ($lang) crashed; last log lines:"
    tail -n 40 "$OUT/$lang.log"
    status=1
  fi
done
exit "$status"
