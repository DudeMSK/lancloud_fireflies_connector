# Развёртывание на сервере (Linux, systemd)

Инструкция для IT-отдела: разворачивает `lancloud_fireflies_bridge.py` как
системную службу, которая работает постоянно и переживает перезагрузки сервера.

Что делает скрипт: раз в минуту опрашивает 4 календаря в Exchange (LanCloud),
находит встречи со ссылкой на Teams и в момент начала отправляет их в
Fireflies API, чтобы бот-транскрайбер автоматически зашёл в звонок.

## Требования

- Linux-сервер, Python 3.9+
- Исходящий доступ (443/tcp) до `mail.lancloud.ru` и `api.fireflies.ai` — без
  него скрипт не сможет ни читать календарь, ни слать запросы в Fireflies
- Права root (или sudo) для установки systemd-службы

## Шаг 1. Перенести файлы проекта на сервер

Предпочтительный способ — клонировать из GitHub (сразу без секретов в истории):

```bash
sudo mkdir -p /opt/lancloud-fireflies-bridge
sudo git clone https://github.com/DudeMSK/lancloud_fireflies_connector.git /opt/lancloud-fireflies-bridge
```

**Если вместо этого копируете файлы напрямую с рабочей машины (scp/rsync/архивом) —
ВАЖНО:** не копируйте файлы `.env` и `.env.example` этим способом — в рабочей
копии `.env.example` на момент подготовки этой инструкции лежат реальные
пароль и API-ключ (артефакт локальной разработки, ещё не отправленный в
GitHub). Перенесите всё, кроме этих двух файлов, а `.env` создайте заново
на сервере вручную — см. шаг 3.

## Шаг 2. Установить зависимости и systemd-службу

```bash
cd /opt/lancloud-fireflies-bridge
sudo bash deploy/install.sh
```

Скрипт `deploy/install.sh`:
- создаёт отдельного системного пользователя `fireflies-bridge` (без логина,
  без домашней папки — только для запуска этой службы, минимум прав)
- создаёт виртуальное окружение и ставит зависимости из `requirements.txt`
- копирует `deploy/lancloud-fireflies-bridge.service` в `/etc/systemd/system/`
  и включает автозапуск при загрузке сервера (`systemctl enable`)

## Шаг 3. Создать `.env` на сервере

Файл `/opt/lancloud-fireflies-bridge/.env` не переносится с рабочей машины —
создайте его прямо на сервере:

```bash
sudo tee /opt/lancloud-fireflies-bridge/.env > /dev/null <<'EOF'
EWS_EMAIL=secretary@enremservice.ru
EWS_USERNAME=secretary@enremservice.ru
EWS_PASSWORD=<реальный пароль>
EWS_SERVER=mail.lancloud.ru
TZ=Europe/Moscow
FIREFLIES_API_KEY=<реальный ключ>
EOF
sudo chown fireflies-bridge:fireflies-bridge /opt/lancloud-fireflies-bridge/.env
sudo chmod 600 /opt/lancloud-fireflies-bridge/.env
```

(Актуальные значения пароля и ключа — у постановщика задачи, передавать их
стоит доверенным каналом, не через тикет/чат открытым текстом.)

## Шаг 4. Запустить и проверить

```bash
sudo systemctl start lancloud-fireflies-bridge
sudo systemctl status lancloud-fireflies-bridge
```

В статусе должно быть `active (running)`. Если нет — смотрите логи (ниже) и
проверяйте `.env`.

## Повседневное управление

```bash
sudo systemctl status lancloud-fireflies-bridge     # состояние
sudo systemctl stop lancloud-fireflies-bridge        # остановить
sudo systemctl restart lancloud-fireflies-bridge     # перезапустить (например, после правки .env)
sudo systemctl disable lancloud-fireflies-bridge     # отключить автозапуск
journalctl -u lancloud-fireflies-bridge -f           # логи в реальном времени
journalctl -u lancloud-fireflies-bridge --since "1 hour ago"   # логи за период
```

Служба настроена на автоперезапуск при падении (`Restart=always` в unit-файле)
и на автозапуск при загрузке сервера — вручную поднимать после перезагрузки
не нужно.

## Обновление кода в будущем

```bash
cd /opt/lancloud-fireflies-bridge
sudo git pull
sudo -u fireflies-bridge venv/bin/pip install -r requirements.txt
sudo systemctl restart lancloud-fireflies-bridge
```
