#!/usr/bin/env bash
# Установка lancloud-fireflies-bridge как systemd-службы.
# Запускать от root (sudo) НА СЕРВЕРЕ, после того как файлы проекта уже
# скопированы в /opt/lancloud-fireflies-bridge (см. DEPLOYMENT.md).
set -euo pipefail

APP_DIR="/opt/lancloud-fireflies-bridge"
SERVICE_USER="fireflies-bridge"
SERVICE_NAME="lancloud-fireflies-bridge"

if [ "$(id -u)" -ne 0 ]; then
  echo "Запускать от root: sudo bash deploy/install.sh" >&2
  exit 1
fi

if [ ! -f "$APP_DIR/lancloud_fireflies_bridge.py" ]; then
  echo "Не найден $APP_DIR/lancloud_fireflies_bridge.py." >&2
  echo "Сначала скопируйте туда файлы проекта (см. DEPLOYMENT.md, шаг 1)." >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 не найден. Установите его, например:" >&2
  echo "  Debian/Ubuntu: apt update && apt install -y python3 python3-venv python3-pip" >&2
  echo "  RHEL/CentOS/Alma: dnf install -y python3 python3-pip" >&2
  exit 1
fi

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
  echo "Создан системный пользователь $SERVICE_USER (без логина, без домашней папки)."
fi

if [ ! -d "$APP_DIR/venv" ]; then
  python3 -m venv "$APP_DIR/venv"
  echo "Создано виртуальное окружение $APP_DIR/venv"
fi

"$APP_DIR/venv/bin/pip" install --upgrade pip -q
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q
echo "Зависимости из requirements.txt установлены."

if [ ! -f "$APP_DIR/.env" ]; then
  echo ""
  echo "!!! ВНИМАНИЕ: $APP_DIR/.env не найден."
  echo "!!! Служба не сможет подключиться к Exchange/Fireflies без него."
  echo "!!! Создайте его вручную ПРЯМО НА СЕРВЕРЕ (см. DEPLOYMENT.md, шаг 3) — не переносите .env"
  echo "!!! с рабочей машины по недоверенному каналу."
fi

chown -R "$SERVICE_USER":"$SERVICE_USER" "$APP_DIR"
[ -f "$APP_DIR/.env" ] && chmod 600 "$APP_DIR/.env"

cp "$APP_DIR/deploy/lancloud-fireflies-bridge.service" "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"

echo ""
echo "=== Установка завершена ==="
echo "Дальше:"
echo "  1. Проверьте, что $APP_DIR/.env заполнен реальными данными (EWS_EMAIL, EWS_USERNAME,"
echo "     EWS_PASSWORD, EWS_SERVER, TZ, FIREFLIES_API_KEY)."
echo "  2. Запустить службу:   systemctl start $SERVICE_NAME"
echo "  3. Проверить статус:   systemctl status $SERVICE_NAME"
echo "  4. Смотреть логи:      journalctl -u $SERVICE_NAME -f"
