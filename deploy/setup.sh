#!/bin/bash
# best.shoponph.online 사이트 + 회원 서버 설치 (다른 사업 폴더·설정은 건드리지 않음)
set -e
D=/root/besttour
DATA=/root/besttour-data
DOMAIN=${DOMAIN:-best.shoponph.online}
if [ -d "$D/.git" ]; then git -C "$D" pull -q --ff-only; else git clone -q https://github.com/Shop-on-88/besttour "$D"; fi
mkdir -p /var/www/besttour "$DATA"
chmod 700 "$DATA"
rsync -a --delete "$D/site/" /var/www/besttour/

# 회원 서버 (포트 8091, 이 서버 안에서만 접속)
cat > /etc/systemd/system/besttour-api.service <<SVC
[Unit]
Description=Best Tour API
After=network.target

[Service]
Environment=BEST_DATA=$DATA
Environment=BEST_PORT=8091
ExecStart=/usr/bin/python3 $D/server/app.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
SVC
systemctl daemon-reload
systemctl enable -q besttour-api
systemctl restart besttour-api

# nginx 설정
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
mkdir -p /etc/nginx/snippets
cat > /etc/nginx/snippets/besttour-api.conf <<'NGX'
location /api/ {
    proxy_pass http://127.0.0.1:8091;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    client_max_body_size 200k;
}
NGX
grep -q 'besttour-api.conf' "$CONF" || sed -i 's|^\(\s*\)root /var/www/besttour;|\1root /var/www/besttour;\n\1include /etc/nginx/snippets/besttour-api.conf;|' "$CONF"
nginx -t
systemctl reload nginx
grep -q "listen 443" "$CONF" || certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --register-unsafely-without-email --redirect || echo "인증서 발급 실패: 몇 분 뒤 이 명령을 다시 실행하세요."

# 5분마다 자동 반영 (서버 코드가 바뀌면 회원 서버도 재시작)
chmod +x "$D/deploy/sync.sh"
( crontab -l 2>/dev/null | grep -v 'besttour-sync' ; echo "*/5 * * * * $D/deploy/sync.sh # besttour-sync" ) | crontab -

sleep 2
if curl -fs http://127.0.0.1:8091/api/health >/dev/null; then echo "회원 서버 정상 작동"; else echo "회원 서버 오류: journalctl -u besttour-api -n 30"; fi
echo "완료: https://$DOMAIN"
echo "----------------------------------------"
cat "$DATA/admin.txt" 2>/dev/null || true
echo "----------------------------------------"
