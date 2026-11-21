#!/bin/bash
# 깃허브 변경사항 자동 반영
D=/root/besttour
OLD=$(git -C "$D" rev-parse HEAD:server 2>/dev/null)
git -C "$D" pull -q --ff-only || exit 0
rsync -a --delete "$D/site/" /var/www/besttour/
NEW=$(git -C "$D" rev-parse HEAD:server 2>/dev/null)
if [ "$OLD" != "$NEW" ] && systemctl is-enabled -q besttour-api 2>/dev/null; then systemctl restart besttour-api; fi
