# lancloud-fireflies-bridge

Автоматически читает Teams-ссылки из корпоративного Exchange-календаря (LanCloud, EWS)
и в момент начала встречи отправляет их в Fireflies.ai (`addToLiveMeeting`), чтобы
бот Fred подключался к звонку сам, без ручного нажатия кнопки в десктоп-приложении.

```
LanCloud Exchange Calendar → Python (exchangelib) → Teams URL → Fireflies API
```

## Ограничения (важно прочитать до внедрения)

1. Доступ к API Fireflies есть только на тарифах **Business/Enterprise**.
2. `addToLiveMeeting` ограничен **3 запросами за 20 минут** — при частом графике
   встреч скрипт может начать отставать, это не обходится программно.
3. Работает **только** для встреч, уже стоящих в календаре. Спонтанные
   «Meet now» звонки в Teams календарных событий не создают — этим способом
   их поймать нельзя.
4. Для EWS нужны реальные учётные данные и (желательно) адрес сервера LanCloud
   (`mail.lancloud.ru` — уже известен из OWA, но если autodiscover не сработает,
   уточните у ИТ-поддержки LanCloud точный EWS-эндпоинт).

## Установка

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Настройка

1. Скопируйте `.env.example` в `.env`:
   ```bash
   cp .env.example .env
   ```
2. Заполните `.env` своими данными:
   ```
   EWS_EMAIL=example@company.ru
   EWS_USERNAME=example@company.ru
   EWS_PASSWORD=ваш_пароль
   EWS_SERVER=mail.example.ru
   FIREFLIES_API_KEY=ваш_ключ_fireflies
   ```
3. **Не коммитьте `.env` в git** — он уже в `.gitignore`, но перепроверьте перед публикацией.

## Запуск

```bash
python3 lancloud_fireflies_bridge.py
```

Скрипт:
- подключается к Exchange (сначала пробует autodiscover, при неудаче — явный `EWS_SERVER`);
- каждые 60 секунд опрашивает календарь на ближайшие встречи;
- ищет ссылку `teams.microsoft.com/l/meetup-join/...` (корпоративный Teams) или
  `teams.live.com/meet/...` (личный/бесплатный Teams) в месте или теле встречи;
- за 1 минуту до начала отправляет её в Fireflies;
- запоминает уже обработанные встречи в `processed_meetings.json`, чтобы не дублировать запросы;
- через `VERIFY_DELAY_MINUTES` (по умолчанию 5) минут после окончания встречи спрашивает
  Fireflies API, появился ли транскрипт — это подтверждает, что бот реально подключился
  к звонку, а не просто что запрос был принят. Если транскрипт не найден, повторяет
  проверку ещё `VERIFY_MAX_ATTEMPTS` раз (по умолчанию 3) с интервалом `VERIFY_RETRY_MINUTES`
  (по умолчанию 5) минут, а затем пишет в лог WARNING, если бот так и не подключился
  (например, из-за зала ожидания Teams).

## Структура проекта

```
lancloud-fireflies-bridge/
├── lancloud_fireflies_bridge.py   # основной скрипт
├── requirements.txt               # зависимости
├── .env.example                   # шаблон переменных окружения
├── .gitignore                     # исключает .env и служебные файлы
└── README.md                      # этот файл
```

## Автозапуск (опционально)

Чтобы скрипт работал постоянно в фоне:
- **Windows**: создать задачу в Планировщике заданий (Task Scheduler), запуск при входе в систему.
- **Linux/macOS**: обернуть в systemd unit / launchd job, либо запускать через `nohup`/`screen`/`tmux`.

Готовая инструкция для развёртывания на Linux-сервере как systemd-службы (с
установочным скриптом и unit-файлом) — см. [DEPLOYMENT.md](DEPLOYMENT.md).