#!/usr/bin/env python3
"""
Быстрая проверка: получается ли подключиться к Exchange (LanCloud) и увидеть
ближайшие встречи в календаре. Не трогает Fireflies — только читает календарь.

Запуск:
    python3 test_connection.py
"""

import os
import re
from datetime import timedelta, datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name(".env"))
except ImportError:
    pass

from exchangelib import Credentials, Account, Configuration, DELEGATE, EWSTimeZone, BASIC

EWS_EMAIL = os.environ.get("EWS_EMAIL", "")
EWS_USERNAME = os.environ.get("EWS_USERNAME", "")
EWS_PASSWORD = os.environ.get("EWS_PASSWORD", "")
EWS_SERVER = os.environ.get("EWS_SERVER", "")
# Часовой пояс для отображения времени встреч. Если не задан в .env — пробуем
# определить автоматически, но на некоторых системах это определяется неверно,
# поэтому лучше явно прописать TZ=Europe/Moscow в .env.
TZ_NAME = os.environ.get("TZ", "")

ROOM_NAME_PATTERN = re.compile(r"Групп\s+(\S+)\s*\(")


def extract_room_name(event):
    """Достаём название переговорной из поля "Где", например:
    'ЭРС Групп Магадан (2 этаж)' -> 'Магадан'
    """
    location = str(event.location or "")
    match = ROOM_NAME_PATTERN.search(location)
    return match.group(1) if match else None


def build_title(event, local_start) -> str:
    """Собирает title в том же формате, что и основной скрипт при отправке в Fireflies."""
    subject = event.subject or "Встреча без названия"
    room = extract_room_name(event)
    date_time = local_start.strftime("%d.%m.%Y - %H:%M")
    return f"{subject} - {room} - ({date_time})" if room else f"{subject} - ({date_time})"


def try_connect() -> Account:
    # LanCloud (mail.lancloud.ru) проверено: явный сервер + BASIC-аутентификация.
    print(f"Подключаемся к '{EWS_SERVER}' с логином '{EWS_USERNAME}' (BASIC)...")
    try:
        creds = Credentials(username=EWS_USERNAME, password=EWS_PASSWORD)
        config = Configuration(server=EWS_SERVER, credentials=creds, auth_type=BASIC)
        account = Account(
            primary_smtp_address=EWS_EMAIL,
            config=config,
            autodiscover=False,
            access_type=DELEGATE,
        )
        # Форсируем реальный запрос к серверу сейчас, а не полагаемся на ленивую
        # инициализацию exchangelib — иначе ошибка авторизации всплывёт позже.
        _ = account.root
        print("✓ Подключение успешно.")
        return account
    except Exception as e:
        raise SystemExit(
            f"✗ Не удалось подключиться: {e}\n\n"
            "Проверьте:\n"
            "  1. EWS_USERNAME и EWS_PASSWORD в .env (без лишних пробелов/комментариев в строке).\n"
            "  2. EWS_SERVER указан верно (mail.lancloud.ru).\n"
            "  3. Пароль не устарел и не требует отдельного app-пароля."
        )


def main():
    if not all([EWS_EMAIL, EWS_USERNAME, EWS_PASSWORD, EWS_SERVER]):
        print("Заполните .env (EWS_EMAIL, EWS_USERNAME, EWS_PASSWORD, EWS_SERVER) перед запуском.")
        raise SystemExit(1)

    account = try_connect()

    print("\nЧитаем ближайшие 7 дней календаря...")
    tz = EWSTimeZone(TZ_NAME) if TZ_NAME else EWSTimeZone.localzone()
    print(f"(используем часовой пояс: {tz})")
    now = datetime.now(tz)
    items = account.calendar.view(start=now, end=now + timedelta(days=7))

    count = 0
    for event in items:
        count += 1
        local_start = event.start.astimezone(tz)
        title_preview = build_title(event, local_start)
        print(f"   - {title_preview}\n")
        # print(f"     [debug] location='{event.location}'")
        if count >= 20:
            print("   ... (показаны первые 20)")
            break

    if count == 0:
        print("   Встреч в ближайшие 7 дней не найдено (это может быть нормально).")

    print("\nГотово. Подключение и чтение календаря работают.")


if __name__ == "__main__":
    main()