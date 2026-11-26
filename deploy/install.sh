#!/bin/sh
# Turn on job-hunter's background parts. Run again after updating these files.
#   sh deploy/install.sh          install and start
#   sh deploy/install.sh remove   stop and remove
set -e
UNIT_DIR="$HOME/.config/systemd/user"
UNITS="job-hunter-web.service job-hunter-bot.service job-hunter-tick.service job-hunter-tick.timer"
cd "$(dirname "$0")"
if [ "$1" = "remove" ]; then
  systemctl --user disable --now job-hunter-web.service job-hunter-bot.service job-hunter-tick.timer || true
  for u in $UNITS; do rm -f "$UNIT_DIR/$u"; done
  systemctl --user daemon-reload
  echo "Removed."
  exit 0
fi
mkdir -p "$UNIT_DIR"
for u in $UNITS; do cp "$u" "$UNIT_DIR/$u"; done
systemctl --user daemon-reload
systemctl --user enable --now job-hunter-web.service job-hunter-bot.service job-hunter-tick.timer
echo "Installed. Website: http://127.0.0.1:8000"
systemctl --user --no-pager list-timers job-hunter-tick.timer
