#!/bin/bash
# Deploy Alfagift Data ke Raspberry Pi: web app (port 9094) + penjadwal crawl di dalam app.
#
# Kredensial ada di .deploy.env (tidak ikut git):
#   PI_HOST=192.168.110.90
#   PI_USER=pi
#   PI_PASS=...
#   APP_PASSWORD=         # password login web (kosong = tanpa login)
#   ALFA_PHONE=...        # akun Alfagift (alamat aktif menentukan toko & harga)
#   ALFA_PASSWORD=...
#   PUBLIC_HOST=...       # opsional, mis. alfagift.csmartkubu.uk → didaftarkan ke Cloudflare Tunnel
set -e
cd "$(dirname "$0")"
[ -f .deploy.env ] || { echo "File .deploy.env belum ada — lihat komentar di deploy.sh." >&2; exit 1; }
source .deploy.env

PI_PORT=9094
PI_DIR="/home/pi/alfagift-data"
TUNNEL="csmart"
SSH="sshpass -p $PI_PASS ssh -o StrictHostKeyChecking=no $PI_USER@$PI_HOST"

command -v sshpass >/dev/null || brew install sshpass

echo "==> Menyalin file ke Pi..."
sshpass -p "$PI_PASS" rsync -az \
  --exclude='.DS_Store' --exclude='__pycache__' --exclude='data/' \
  --exclude='.deploy.env' --exclude='.git' --exclude='deploy.sh' \
  -e "ssh -o StrictHostKeyChecking=no" ./ "$PI_USER@$PI_HOST:$PI_DIR/"

echo "==> Service web..."
$SSH "sudo tee /etc/systemd/system/alfagift-data.service > /dev/null" <<UNIT
[Unit]
Description=Alfagift Data (web)
After=network.target

[Service]
User=$PI_USER
WorkingDirectory=$PI_DIR
Environment=PORT=$PI_PORT
Environment=APP_PASSWORD=$APP_PASSWORD
Environment="ALFA_PHONE=$ALFA_PHONE"
Environment="ALFA_PASSWORD=$ALFA_PASSWORD"
ExecStart=/usr/bin/python3 $PI_DIR/app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

$SSH "sudo chmod 600 /etc/systemd/system/alfagift-data.service && sudo systemctl daemon-reload && \
      sudo systemctl enable alfagift-data && sudo systemctl restart alfagift-data"

if [ -n "$PUBLIC_HOST" ]; then
  echo "==> Cloudflare Tunnel: $PUBLIC_HOST..."
  CHANGED=$($SSH "sudo python3 - <<'PY'
import re, shutil, time
path = '/etc/cloudflared/config.yml'
s = open(path).read()
if '$PUBLIC_HOST' in s:
    print('unchanged')
else:
    shutil.copy(path, path + '.bak-' + time.strftime('%Y%m%d%H%M%S'))
    m = re.search(r'^([ \t]*)- service:\s*http_status:404', s, re.M)
    if not m: raise SystemExit('Rule catch-all http_status:404 tidak ditemukan')
    ind = m.group(1)
    s = s[:m.start()] + f'{ind}- hostname: $PUBLIC_HOST\n{ind}  service: http://localhost:$PI_PORT\n' + s[m.start():]
    open(path, 'w').write(s)
    print('changed')
PY")
  if [ "$CHANGED" = "changed" ]; then
    $SSH "cloudflared tunnel route dns $TUNNEL $PUBLIC_HOST || sudo cloudflared tunnel route dns $TUNNEL $PUBLIC_HOST"
    $SSH "sudo systemctl restart cloudflared"
  fi
fi

echo ""
$SSH "systemctl is-active alfagift-data"
echo "✅ Deploy selesai — LAN: http://$PI_HOST:$PI_PORT${PUBLIC_HOST:+ · Publik: https://$PUBLIC_HOST}"
