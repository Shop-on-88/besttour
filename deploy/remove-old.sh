#!/bin/bash
# 기존 임시 주소 best.shoponph.online 을 베스트여행사 사이트에서 제거하고 bestviptour.com 만 남김
# (다른 사업의 nginx 설정·인증서는 건드리지 않음)
set -e
CONF=/etc/nginx/sites-available/besttour
LIVE=/etc/letsencrypt/live/bestviptour.com
BAK=/root/besttour-data/nginx-besttour.bak.$(date +%Y%m%d%H%M%S)

if [ ! -f "$LIVE/fullchain.pem" ]; then
  echo "bestviptour.com 인증서가 없습니다. 먼저 add-domain.sh 를 실행하세요."; exit 1
fi
cp "$CONF" "$BAK"

OPT=""; [ -f /etc/letsencrypt/options-ssl-nginx.conf ] && OPT="    include /etc/letsencrypt/options-ssl-nginx.conf;"
DH="";  [ -f /etc/letsencrypt/ssl-dhparams.pem ] && DH="    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;"

cat > "$CONF" <<NGX
server {
    listen 80;
    server_name bestviptour.com www.bestviptour.com;
    return 301 https://bestviptour.com\$request_uri;
}
server {
    listen 443 ssl;
    server_name bestviptour.com www.bestviptour.com;
    ssl_certificate $LIVE/fullchain.pem;
    ssl_certificate_key $LIVE/privkey.pem;
$OPT
$DH
    root /var/www/besttour;
    index index.html;
    include /etc/nginx/snippets/besttour-api.conf;
    add_header X-Robots-Tag "noindex, nofollow" always;
    location / { try_files \$uri \$uri/ /index.html; }
}
NGX

if ! nginx -t; then
  echo "설정 오류가 있어 원래대로 되돌립니다."; cp "$BAK" "$CONF"; nginx -t && systemctl reload nginx; exit 1
fi
systemctl reload nginx

# 기존 주소 인증서 삭제 (있을 때만)
if [ -d /etc/letsencrypt/live/best.shoponph.online ]; then
  certbot delete --cert-name best.shoponph.online --non-interactive || true
fi

sleep 1
if curl -fs https://bestviptour.com/api/health >/dev/null; then
  echo "완료: 기존 주소를 지웠고 https://bestviptour.com 정상 작동 중"
else
  echo "확인 필요: https://bestviptour.com 이 응답하지 않습니다. 원래 설정 백업: $BAK"
fi
