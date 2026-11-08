#!/usr/bin/env bash
# Keep a Bluetooth speaker connected: check every 10 s, reconnect if it dropped.
# Usage: bash setup/jbl-reconnect.sh AA:BB:CC:DD:EE:FF   (run by jbl-reconnect@.service)
set -u
mac="${1:?usage: jbl-reconnect.sh <speaker Bluetooth address>}"
while true; do
  if ! bluetoothctl info "$mac" 2>/dev/null | grep -q "Connected: yes"; then
    bluetoothctl connect "$mac" >/dev/null 2>&1 || true
  fi
  sleep 10
done
