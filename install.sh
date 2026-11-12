#!/usr/bin/env bash
# RoboMorph installer — run as root on the VPS from inside the robomorph folder:
#   bash install.sh
set -euo pipefail
DIR=/opt/robomorph
SRC="$(cd "$(dirname "$0")" && pwd)"

apt-get update -y
apt-get install -y ffmpeg python3 python3-requests fonts-dejavu-core

mkdir -p "$DIR"
cp "$SRC"/robomorph.py "$SRC"/topics.json "$DIR"/
[ -f "$DIR/.env" ] || cp "$SRC"/.env.example "$DIR/.env"
mkdir -p "$DIR"/{ready,inbox,posted,logs}

cat > /etc/systemd/system/robomorph-post.service <<EOF
[Unit]
Description=RoboMorph: post one Reel to Facebook
After=network-online.target
[Service]
Type=oneshot
WorkingDirectory=$DIR
ExecStart=/usr/bin/python3 $DIR/robomorph.py post
TimeoutStartSec=3600
EOF

cat > /etc/systemd/system/robomorph-post.timer <<EOF
[Unit]
Description=RoboMorph posting 07:00, 12:00, 19:00 WITA
[Timer]
OnCalendar=*-*-* 07,12,19:00:00 Asia/Makassar
Persistent=true
[Install]
WantedBy=timers.target
EOF

cat > /etc/systemd/system/robomorph-produce.service <<EOF
[Unit]
Description=RoboMorph: keep the reserve of ready videos full
After=network-online.target
[Service]
Type=oneshot
WorkingDirectory=$DIR
ExecStart=/usr/bin/python3 $DIR/robomorph.py produce
TimeoutStartSec=3600
EOF

cat > /etc/systemd/system/robomorph-produce.timer <<EOF
[Unit]
Description=RoboMorph refill every 3 hours
[Timer]
OnCalendar=*-*-* 00/3:30:00
Persistent=true
[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now robomorph-post.timer robomorph-produce.timer

echo
echo "Installed in $DIR"
echo "Next: nano $DIR/.env   (fill in the keys)"
echo "Then test: cd $DIR && python3 robomorph.py produce 1 && python3 robomorph.py status"
systemctl list-timers 'robomorph*' --no-pager || true
