#!/usr/bin/env python3
"""
Быстрая проверка: получается ли подключиться к Exchange (LanCloud) и увидеть
ближайшие встречи во ВСЕХ опрашиваемых календарях (свой + чужие из
lancloud_fireflies_bridge.OTHER_CALENDAR_MAILBOXES). Не трогает Fireflies —
только читает календари, теми же функциями, что использует основной скрипт,
поэтому список календарей всегда совпадает с тем, что реально опрашивается.

Запуск:
    python3 test_connection.py
"""

from datetime import timedelta, datetime

import lancloud_fireflies_bridge as bridge
from exchangelib import EWSTimeZone
from exchangelib.errors import UnauthorizedError, ErrorAccessDenied, ErrorFolderNotFound, ErrorNonExistentMailbox

MAX_EVENTS_PER_CALENDAR = None  # None = показывать все встречи без ограничения


def build_title(event, local_start) -> str:
    """Собирает title в том же формате, что и основной скрипт при отправке в Fireflies."""
    subject = event.subject or "Встреча без названия"
    room = bridge.extract_room_name(event)
    date_time = local_start.strftime("%d.%m.%Y - %H:%M")
    return f"{subject} - {room} - ({date_time})" if room else f"{subject} - ({date_time})"


def main():
    print(f"Подключаемся к '{bridge.EWS_SERVER}' с логином '{bridge.EWS_USERNAME}' (BASIC)...")
    try:
        config = bridge.connect_and_verify()
    except RuntimeError as e:
        raise SystemExit(f"✗ Не удалось подключиться: {e}")
    print("✓ Подключение успешно.")

    calendar_accounts = bridge.build_calendar_accounts(config)
    tz = EWSTimeZone(bridge.TZ_NAME) if bridge.TZ_NAME else EWSTimeZone.localzone()
    print(f"(используем часовой пояс: {tz})")
    print(f"\nЧитаем ближайшие 7 дней в {len(calendar_accounts)} календарях: "
          f"{', '.join(label for label, _ in calendar_accounts)}")

    now = datetime.now(tz)
    total = 0
    for label, account in calendar_accounts:
        print(f"\n--- {label} ---")
        try:
            items = list(account.calendar.view(start=now, end=now + timedelta(days=7)))
        except (UnauthorizedError, ErrorAccessDenied, ErrorFolderNotFound, ErrorNonExistentMailbox) as e:
            print(f"   ✗ нет доступа к этому календарю: {e}")
            continue

        if not items:
            print("   Встреч в ближайшие 7 дней не найдено (это может быть нормально).")
            continue

        for event in items[:MAX_EVENTS_PER_CALENDAR]:
            local_start = event.start.astimezone(tz)
            print(f"   - {build_title(event, local_start)}")
        if MAX_EVENTS_PER_CALENDAR is not None and len(items) > MAX_EVENTS_PER_CALENDAR:
            print(f"   ... ещё {len(items) - MAX_EVENTS_PER_CALENDAR} (показаны первые {MAX_EVENTS_PER_CALENDAR})")
        total += len(items)

    print(f"\nВсего встреч во всех календарях за 7 дней: {total}")
    print("Готово. Подключение и чтение календарей работают.")


if __name__ == "__main__":
    main()
