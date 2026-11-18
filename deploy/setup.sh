#!/bin/bash
# best.shoponph.online 임시 사이트 설치 (다른 사업 폴더·설정은 건드리지 않음)
set -e
D=/root/besttour
DOMAIN=best.shoponph.online
if [ -d "$D/.git" ]; then git -C "$D" pull -q --ff-only; else git clone -q https://github.com/Shop-on-88/besttour "$D"; fi
mkdir -p /var/www/besttour
rsync -a --delete "$D/site/" /var/www/besttour/
CONF=/etc/nginx/sites-available/besttour
if [ ! -f "$CONF" ]; then
cat > "$CONF" <<NGX
server {
    listen 80;
    server_name $DOMAIN;
    root /var/www/besttour;
    index index.html;
    add_header X-Robots-Tag "noindex, nofollow" always;
    location / { try_files \$uri \$uri/ /index.html; }
}
NGX
ln -sf "$CONF" /etc/nginx/sites-enabled/besttour
fi
nginx -t
systemctl reload nginx
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --register-unsafely-without-email --redirect || echo "인증서 발급 실패: 주소 연결이 아직 퍼지는 중일 수 있습니다. 몇 분 뒤 이 명령을 다시 실행하세요."
( crontab -l 2>/dev/null | grep -v 'besttour-sync' ; echo '*/5 * * * * git -C /root/besttour pull -q --ff-only && rsync -a --delete /root/besttour/site/ /var/www/besttour/ # besttour-sync' ) | crontab -
echo "완료: https://$DOMAIN"
