#!/usr/bin/env bash
# Run ONCE on the VPS as root after pushing code with push.sh.
# Usage: bash /opt/policy-maker/deploy/server_setup.sh
set -e

APP_DIR="/opt/policy-maker/src/server"
VENV_DIR="/opt/policy-maker/venv"
DOMAIN="policy.lewtucker.net"
SERVICE="policy-maker"

echo "==> Installing system packages"
apt-get update -y -q
apt-get install -y -q python3-pip python3-venv nginx certbot python3-certbot-nginx

echo "==> Setting up Python virtualenv"
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install -q --upgrade pip
"$VENV_DIR/bin/pip" install -q -r "$APP_DIR/requirements.txt"

echo "==> Creating .env (edit this file with your values)"
if [ ! -f "$APP_DIR/.env" ]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  # Pre-fill from environment if provided, otherwise placeholders remain
  echo ""
  echo "  *** IMPORTANT: Edit $APP_DIR/.env with your real values before starting ***"
  echo ""
fi

echo "==> Writing systemd service"
cat > /etc/systemd/system/$SERVICE.service << EOF
[Unit]
Description=Policy Maker
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$APP_DIR
Environment=PATH=$VENV_DIR/bin
ExecStart=$VENV_DIR/bin/uvicorn server:app --host 127.0.0.1 --port 8080
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable $SERVICE

echo "==> Writing nginx config"
cat > /etc/nginx/sites-available/$SERVICE << EOF
server {
    listen 80;
    server_name $DOMAIN;

    client_max_body_size 10M;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120s;
    }
}
EOF

ln -sf /etc/nginx/sites-available/$SERVICE /etc/nginx/sites-enabled/$SERVICE
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

echo ""
echo "==> NEXT STEPS:"
echo ""
echo "  1. Edit the .env file:"
echo "     nano $APP_DIR/.env"
echo "     (Set APP_PASSWORD, ANTHROPIC_API_KEY, SESSION_SECRET)"
echo ""
echo "  2. Start the app:"
echo "     systemctl start $SERVICE"
echo "     systemctl status $SERVICE"
echo ""
echo "  3. Get a TLS certificate (DNS must be pointing at this server first):"
echo "     certbot --nginx -d $DOMAIN"
echo ""
echo "  Done! App will be at https://$DOMAIN"
