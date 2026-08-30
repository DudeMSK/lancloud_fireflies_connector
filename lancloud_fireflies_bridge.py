#!/usr/bin/env python3
r"""
LanCloud Exchange Calendar -> Python -> Teams URL -> Fireflies API

Скрипт периодически опрашивает корпоративный Exchange-календарь (хостинг LanCloud,
доступ по протоколу EWS — НЕ через Microsoft Graph, т.к. это не облако Microsoft 365),
находит в предстоящих встречах ссылки teams.microsoft.com и в момент начала встречи
отправляет их в Fireflies.ai через мутацию addToLiveMeeting, чтобы бот Fred
подключился к звонку автоматически.

ВАЖНЫЕ ОГРАНИЧЕНИЯ (уточнить до внедрения):
  1. Доступ к API Fireflies есть только на тарифах Business/Enterprise
     (Settings -> Integrations -> Fireflies API).
  2. addToLiveMeeting ограничен 3 запросами за 20 минут — при частых встречах
     можно упереться в лимит, скрипт это не обходит.
  3. Схема работает ТОЛЬКО для встреч, уже стоящих в календаре. Спонтанные
     "Meet now" звонки в Teams в календаре не создают событий, поэтому этим
     способом их поймать нельзя — для них нужен отдельный механизм
     (например, отслеживание Teams presence/Graph subscription, если политики
     Azure AD когда-нибудь разрешат сторонним приложениям OAuth).
  4. EWS-сервер для LanCloud подтверждён: mail.lancloud.ru, аутентификация BASIC,
     логин — полный email (secretary@... в вашем случае).

Установка зависимостей:
    pip install exchangelib requests python-dotenv

Настройки задаются либо через переменные окружения, либо (проще) через файл
.env рядом со скриптом — см. .env.example, скопируйте его в .env и заполните:
    EWS_EMAIL=you@company.ru
    EWS_USERNAME=you@company.ru        # логин для EWS (для LanCloud — полный email)
    EWS_PASSWORD=...
    EWS_SERVER=mail.lancloud.ru        # обязателен — autodiscover для LanCloud не настроен
    FIREFLIES_API_KEY=...

Файл .env НЕ храните в git/публичных местах — там пароль в открытом виде.

Подключение к Exchange идёт напрямую через явно заданный EWS_SERVER с BASIC-
аутентификацией (проверено и подтверждено рабочим для mail.lancloud.ru).
"""

import os
import re
import time
import json
import logging
from datetime import timedelta, datetime
from pathlib import Path
from typing import Optional

import requests
from exchangelib import Credentials, Account, Configuration, DELEGATE, EWSTimeZone, BASIC
from exchangelib.errors import UnauthorizedError, ErrorNonExistentMailbox

try:
    from dotenv import load_dotenv
    # Ищет файл .env рядом со скриптом и подгружает переменные из него
    # в os.environ (не перезаписывая уже существующие переменные окружения).
    load_dotenv(Path(__file__).with_name(".env"))
except ImportError:
    # python-dotenv не установлен — просто работаем с переменными окружения
    # напрямую (export EWS_EMAIL=... и т.п.), .env-файл в этом случае игнорируется.
    pass

# ---------------- НАСТРОЙКИ ----------------

EWS_EMAIL = os.environ.get("EWS_EMAIL", "")
EWS_USERNAME = os.environ.get("EWS_USERNAME", "")
EWS_PASSWORD = os.environ.get("EWS_PASSWORD", "")
EWS_SERVER = os.environ.get("EWS_SERVER", "")  # напр. mail.lancloud.ru
# Часовой пояс. Не обязателен для корректности работы (сравнение времени встреч
# идёт по абсолютному времени независимо от пояса), но влияет на читаемость логов.
# Если автоопределение системного пояса работает некорректно — задайте явно,
# например TZ=Europe/Moscow в .env.
TZ_NAME = os.environ.get("TZ", "")

FIREFLIES_API_KEY = os.environ.get("FIREFLIES_API_KEY", "")
FIREFLIES_GRAPHQL_URL = "https://api.fireflies.ai/graphql"

# За сколько минут до начала встречи отправлять бота (0 = ровно в момент начала)
JOIN_LEAD_MINUTES = 1
# Как часто опрашивать календарь, секунды
POLL_INTERVAL_SECONDS = 60

# Пост-проверка, что бот реально подключился и записал встречу: сколько минут
# ждать после ОКОНЧАНИЯ встречи, прежде чем первый раз спросить Fireflies API,
# появился ли транскрипт (сразу после конца записи транскрипт ещё не готов).
VERIFY_DELAY_MINUTES = int(os.environ.get("VERIFY_DELAY_MINUTES", "5"))
# Сколько раз повторить проверку, если транскрипт ещё не найден
VERIFY_MAX_ATTEMPTS = int(os.environ.get("VERIFY_MAX_ATTEMPTS", "3"))
# Интервал между повторными проверками, минут
VERIFY_RETRY_MINUTES = int(os.environ.get("VERIFY_RETRY_MINUTES", "5"))
# Файл, куда сохраняются ID уже отправленных встреч, чтобы не дублировать запросы
STATE_FILE = Path(__file__).with_name("processed_meetings.json")

# Покрывает и корпоративный Teams (Microsoft 365: teams.microsoft.com/l/meetup-join/...),
# и личный/бесплатный Teams (teams.live.com/meet/...) — в реальных встречах LanCloud
# ссылки оказались именно во втором формате, старый regex их не находил вообще.
TEAMS_LINK_PATTERN = re.compile(
    r"https://teams\.microsoft\.com/l/meetup-join/[^\s\"'<>]+"
    r"|https://teams\.live\.com/meet/[^\s\"'<>]+"
)

ROOM_NAME_PATTERN = re.compile(r"Групп\s+(\S+)\s*\(")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("lancloud-fireflies-bridge")


def load_state() -> set:
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text()))
        except json.JSONDecodeError:
            return set()
    return set()


def save_state(processed_ids: set) -> None:
    STATE_FILE.write_text(json.dumps(sorted(processed_ids)))


def connect_account() -> Account:
    if not all([EWS_EMAIL, EWS_USERNAME, EWS_PASSWORD, EWS_SERVER]):
        raise RuntimeError(
            "Не заданы переменные окружения EWS_EMAIL / EWS_USERNAME / "
            "EWS_PASSWORD / EWS_SERVER"
        )

    # LanCloud (mail.lancloud.ru) проверено: работает явный сервер + BASIC-аутентификация.
    # Autodiscover для этого домена не настроен, поэтому сразу идём на явный сервер.
    log.info("Подключаемся к '%s' с логином '%s' (BASIC)...", EWS_SERVER, EWS_USERNAME)
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
        # инициализацию exchangelib — иначе ошибка авторизации всплывёт позже,
        # в середине основного цикла.
        _ = account.root
        log.info("Подключение успешно.")
        return account
    except Exception as e:
        raise RuntimeError(
            f"Не удалось подключиться к Exchange ('{EWS_SERVER}', логин '{EWS_USERNAME}', "
            f"BASIC). Проверьте EWS_USERNAME/EWS_PASSWORD в .env. Ошибка: {e}"
        ) from e


def extract_teams_link(event) -> Optional[str]:
    """Ищем ссылку teams.microsoft.com в месте проведения или в теле приглашения."""
    candidates = [
        str(event.location or ""),
        str(getattr(event, "text_body", None) or event.body or ""),
    ]
    for text in candidates:
        match = TEAMS_LINK_PATTERN.search(text)
        if match:
            return match.group(0)
    return None


def extract_room_name(event) -> Optional[str]:
    """Достаём название переговорной из поля "Где", например:
    'ЭРС Групп Магадан (2 этаж)' -> 'Магадан'
    Если формат не совпал — возвращаем None (комната просто не добавится в title).
    """
    location = str(event.location or "")
    match = ROOM_NAME_PATTERN.search(location)
    return match.group(1) if match else None


def send_to_fireflies(meeting_link: str, title: str, duration_minutes: int = 60) -> bool:
    query = """
    mutation AddToLiveMeeting($meeting_link: String, $title: String, $duration: Int) {
      addToLiveMeeting(meeting_link: $meeting_link, title: $title, duration: $duration) {
        success
        message
      }
    }
    """
    variables = {
        "meeting_link": meeting_link,
        "title": title,
        "duration": max(15, min(duration_minutes, 120)),  # ограничения Fireflies: 15-120 мин
    }
    headers = {
        "Authorization": f"Bearer {FIREFLIES_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(
            FIREFLIES_GRAPHQL_URL,
            json={"query": query, "variables": variables},
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        log.error("Ошибка запроса к Fireflies API: %s", e)
        return False

    data = resp.json()
    if data.get("errors"):
        log.error("Fireflies API вернул ошибку для '%s': %s", title, data["errors"])
        return False

    result = data.get("data", {}).get("addToLiveMeeting", {}) or {}
    if result.get("success"):
        log.info("Бот Fireflies отправлен на встречу: %s", title)
        return True

    log.warning("Fireflies не подтвердил успех для '%s': %s", title, result)
    return False


def check_transcript_recorded(meeting_link: str, title: str, from_date: datetime, to_date: datetime) -> bool:
    """Спрашиваем Fireflies API, появился ли транскрипт для этой встречи —
    это единственный способ узнать, что бот реально подключился и записал
    звонок, а не просто что запрос addToLiveMeeting был принят.
    """
    query = """
    query Transcripts($fromDate: DateTime, $toDate: DateTime, $limit: Int) {
      transcripts(fromDate: $fromDate, toDate: $toDate, limit: $limit) {
        id
        title
        meeting_link
      }
    }
    """
    variables = {
        "fromDate": from_date.isoformat(),
        "toDate": to_date.isoformat(),
        "limit": 50,
    }
    headers = {
        "Authorization": f"Bearer {FIREFLIES_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(
            FIREFLIES_GRAPHQL_URL,
            json={"query": query, "variables": variables},
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        log.error("Ошибка запроса транскриптов Fireflies API: %s", e)
        return False

    data = resp.json()
    if data.get("errors"):
        log.error("Fireflies API вернул ошибку при проверке транскрипта '%s': %s", title, data["errors"])
        return False

    transcripts = data.get("data", {}).get("transcripts") or []
    return any(t.get("meeting_link") == meeting_link or t.get("title") == title for t in transcripts)


def process_pending_verifications(pending: list, tz) -> None:
    """Проходит по очереди отправленных встреч и проверяет, появился ли
    транскрипт (значит бот реально подключился). Не найден — повторяем
    попытку позже, после VERIFY_MAX_ATTEMPTS — сдаёмся и пишем warning."""
    if not pending:
        return

    now = datetime.now(tz)
    still_pending = []
    for item in pending:
        if now < item["next_check"]:
            still_pending.append(item)
            continue

        found = check_transcript_recorded(
            item["link"],
            item["title"],
            item["start"] - timedelta(minutes=5),
            item["end"] + timedelta(minutes=VERIFY_DELAY_MINUTES + VERIFY_RETRY_MINUTES * VERIFY_MAX_ATTEMPTS + 5),
        )
        if found:
            log.info("Подтверждено: бот Fireflies подключился и записал встречу '%s'.", item["title"])
            continue

        item["attempts"] += 1
        if item["attempts"] >= VERIFY_MAX_ATTEMPTS:
            log.warning(
                "НЕ ПОДТВЕРЖДЕНО подключение бота к встрече '%s' — транскрипт не найден после %d "
                "попыток. Проверьте вручную (возможно, зал ожидания Teams или лимит запросов Fireflies).",
                item["title"], item["attempts"],
            )
            continue

        item["next_check"] = now + timedelta(minutes=VERIFY_RETRY_MINUTES)
        still_pending.append(item)

    pending[:] = still_pending


def check_calendar_and_dispatch(account: Account, processed_ids: set, pending_verifications: list) -> None:
    tz = EWSTimeZone(TZ_NAME) if TZ_NAME else EWSTimeZone.localzone()
    now = datetime.now(tz)
    window_end = now + timedelta(minutes=JOIN_LEAD_MINUTES + POLL_INTERVAL_SECONDS / 60 + 2)

    items = list(account.calendar.view(start=now - timedelta(minutes=2), end=window_end))
    log.info("Итерация: сейчас %s, найдено встреч в окне: %d", now.strftime("%d.%m %H:%M:%S"), len(items))

    for event in items:
        event_id = str(event.id)
        local_start = event.start.astimezone(tz)
        subject = event.subject or "(без названия)"

        if event_id in processed_ids:
            log.info("  [%s в %s] уже отправлена ранее — пропуск", subject, local_start.strftime("%H:%M"))
            continue

        start = event.start
        if start - timedelta(minutes=JOIN_LEAD_MINUTES) > now:
            log.info("  [%s в %s] ещё рано — пропуск", subject, local_start.strftime("%H:%M"))
            continue

        link = extract_teams_link(event)
        if not link:
            log.info("  [%s в %s] нет Teams-ссылки в месте/теле — пропуск", subject, local_start.strftime("%H:%M"))
            continue

        duration = int((event.end - event.start).total_seconds() / 60) or 60
        subject = event.subject or "Встреча без названия"
        room = extract_room_name(event)
        date_time = local_start.strftime("%d.%m.%Y - %H:%M")
        title = f"{subject} - {room} - ({date_time})" if room else f"{subject} - ({date_time})"

        log.info("Найдена встреча к отправке: %s", title)

        if send_to_fireflies(link, title, duration):
            processed_ids.add(event_id)
            save_state(processed_ids)
            pending_verifications.append({
                "title": title,
                "link": link,
                "start": event.start,
                "end": event.end,
                "next_check": event.end + timedelta(minutes=VERIFY_DELAY_MINUTES),
                "attempts": 0,
            })


def main() -> None:
    log.info("Подключение к Exchange (LanCloud), сервер: %s", EWS_SERVER)
    account = connect_account()
    log.info("Подключено. Мониторинг календаря запущен (опрос каждые %sс).", POLL_INTERVAL_SECONDS)

    processed_ids = load_state()
    pending_verifications: list = []
    tz = EWSTimeZone(TZ_NAME) if TZ_NAME else EWSTimeZone.localzone()

    while True:
        try:
            check_calendar_and_dispatch(account, processed_ids, pending_verifications)
            process_pending_verifications(pending_verifications, tz)
        except UnauthorizedError:
            log.error("Ошибка авторизации в Exchange — проверьте логин/пароль/сервер.")
        except ErrorNonExistentMailbox:
            log.error("Почтовый ящик %s не найден на сервере %s.", EWS_EMAIL, EWS_SERVER)
        except Exception as e:
            log.exception("Непредвиденная ошибка: %s", e)
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()