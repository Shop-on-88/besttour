#!/bin/bash
# bestviptour.com 을 베스트여행사 사이트에 연결 (다른 사업 설정은 건드리지 않음)
set -e
SERVER_IP=147.93.172.132
CONF=/etc/nginx/sites-available/besttour
DOMS="bestviptour.com www.bestviptour.com"

for h in $DOMS; do
  ip=$(getent ahostsv4 "$h" | awk '{print $1}' | head -1)
  if [ "$ip" != "$SERVER_IP" ]; then
    echo "[$h] 아직 서버로 연결되지 않았습니다 (지금 연결된 곳: ${ip:-없음})."
    echo "도메인 DNS 설정을 바꾼 뒤 10~30분 기다렸다가 이 명령을 다시 실행하세요."
    exit 1
  fi
done

if ! grep -q 'bestviptour.com' "$CONF"; then
  sed -i 's/server_name best\.shoponph\.online;/server_name best.shoponph.online bestviptour.com www.bestviptour.com;/' "$CONF"
fi
nginx -t
systemctl reload nginx

certbot --nginx -d bestviptour.com -d www.bestviptour.com --non-interactive --agree-tos --register-unsafely-without-email --redirect --expand

sleep 1
if curl -fs https://bestviptour.com/api/health >/dev/null; then
  echo "완료: https://bestviptour.com 연결 성공"
else
  echo "연결 확인 실패: 몇 분 뒤 https://bestviptour.com 을 열어 보세요."
fi
