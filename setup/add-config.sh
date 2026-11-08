#!/usr/bin/env bash
# Appends the lines from setup/config.txt.snippet that /boot/firmware/config.txt doesn't have yet.
# Safe to run twice. Usage: sudo bash setup/add-config.sh   (then sudo reboot)
set -eu
cfg=/boot/firmware/config.txt
snippet="$(dirname "$0")/config.txt.snippet"
added=0
while IFS= read -r line; do
  line="${line%$'\r'}"
  case "$line" in ''|'#'*) continue ;; esac
  if ! grep -qxF "$line" "$cfg"; then
    [ "$added" = 0 ] && [ -n "$(tail -c1 "$cfg")" ] && echo >> "$cfg"
    echo "$line" >> "$cfg"
    echo "added: $line"
    added=1
  fi
done < "$snippet"
[ "$added" = 0 ] && echo "config.txt already has every CareBot line" || echo "now run: sudo reboot"
