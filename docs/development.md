# Разработка и проверки

[README](../README.md) · [Архитектура](architecture.md)

## Локальная разработка

Нужны Python 3.13 и PostgreSQL 18. Команды выполняются из корня клонированного
репозитория. Если `.env` ещё нет, создайте его из примера:

```powershell
Copy-Item .env.example .env
```

В `.env` задаются настройки подключения к БД. Базу можно запустить отдельно через Compose:

```powershell
docker compose up -d db
```

Для существующего PostgreSQL используются отдельная база `studyflow` и роль с
правом создавать базы: pytest-django создаёт и удаляет тестовую базу. Имя роли,
пароль, адрес и порт указываются в `.env`.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py createsuperuser
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

Конфигурация читается из `.env`; переменные окружения процесса имеют приоритет.
Секреты, локальные данные PostgreSQL и виртуальное окружение исключены из Git.
Время хранится с учётом часового пояса; часовой пояс приложения - UTC.

## Веб-интерфейс

Главная страница `/` использует `users/templates/studyflow/index.html` и файлы
из `users/static/studyflow/`. `app.js` управляет экранами и формами, `api.js` -
запросами и JWT. Шаблон и статика обслуживаются Django; дополнительный сервер,
сборка и npm-зависимости не нужны. Статические файлы включаются в `collectstatic`.

Для тестов JavaScript нужен Node.js 24 на машине разработчика:

```powershell
node --check users/static/studyflow/app.js
node --check users/static/studyflow/api.js
node --test tests/frontend/api.test.mjs
```

Тесты проверяют обновление токенов при параллельных запросах, смену аккаунта,
выход, временные сетевые ошибки и ограничение адресов текущим API. Они используют
встроенный `node:test` и подменённый `fetch`. Покрытие Python не включает JavaScript.
Для проверки реальных форм используйте [сценарий интерфейса](demo.md#работа-через-интерфейс).

## Проверки

```powershell
docker compose exec web python -m pytest -q
docker compose exec web ruff check .
docker compose exec web ruff format --check .
docker compose exec web python manage.py check
docker compose exec web python manage.py makemigrations --check --dry-run
docker compose exec web python manage.py spectacular --validate --fail-on-warn --file /tmp/schema.yaml
```

При локальном запуске те же команды выполняются через `.venv\Scripts\python.exe`
и `.venv\Scripts\ruff.exe`; для схемы можно указать файл в `.local`.
Тесты используют PostgreSQL и миграции проекта. Проверяются собственная модель
пользователя, вход в админку, доступность Swagger и корректность OpenAPI,
а также ответы health endpoint при доступной и недоступной БД. Тесты авторизации
проверяют регистрацию, ограничения прав, получение и обновление JWT, отключённых
пользователей и доступ только к собственному профилю. Тесты курсов проверяют
изоляцию данных по роли и зачислению, права владельца, защиту от подмены полей,
повторного зачисления и изменения роли при действующем JWT.
Для заданий проверяются видимость черновиков, публикация, дедлайны, допустимые
баллы, привязка к курсу и запрет удаления опубликованных заданий.
Тесты решений проверяют приватность работ, доступ после зачисления и публикации,
дедлайны, исправления, защиту полей и ограничение одной работы на студента.
Для оценок проверяются права преподавателя, границы баллов, повторная проверка,
видимость обратной связи и запрет изменения проверенной работы. Отдельные тесты
проверяют параллельную отправку решений, оценивание с одновременным исправлением
ответа и изменение максимального балла во время проверки.
Проверки прогресса и списков охватывают расчёты, границы страниц, поиск, фильтрацию
и приватность. Тесты числа SQL-запросов проверяют отсутствие N+1 при росте группы
и количества решений. Для демоданных проверяются повторное заполнение,
сохранение изменённых работ и паролей, откат при конфликте аккаунтов,
обновление сроков и соответствие данных правам доступа API.

## CI

Workflow `.github/workflows/ci.yml` запускается при push в `main`, открытии
pull request в `main`, новых коммитах в таком PR и его повторном открытии.
Также доступен ручной запуск через **Actions → CI → Run workflow**.
Обычный push в рабочую ветку без PR не запускает проверки.

Четыре job выполняются независимо на Ubuntu 24.04:

| Job | Проверки |
| --- | --- |
| `Lint` | Ruff: ошибки, импорты и форматирование |
| `Frontend` | Node.js 24: синтаксис JavaScript и тесты API-клиента без установки npm-зависимостей |
| `Tests` | Python 3.13, PostgreSQL 18, совместимость зависимостей, проверки Django, отсутствие пропущенных миграций, применение миграций с нуля, валидация OpenAPI, сбор статики и pytest с покрытием |
| `Docker image` | Сборка образа из Dockerfile; контейнер приложения в этом job не запускается |

Зависимости устанавливаются из зафиксированного `requirements-dev.txt`, загрузки
pip кешируются. Новые запуски отменяют предыдущий незавершённый запуск для той же
ветки или PR. Actions закреплены полными SHA коммитов. Workflow имеет только
`contents: read` и не публикует образ в registry.

Тестовая БД создаётся отдельно на каждом runner. Workflow содержит временные
значения настроек, поэтому секреты репозитория и локальный `.env` для CI не нужны.
Сервис PostgreSQL должен пройти healthcheck до начала шагов тестирования.

Покрытие измеряется для `config`, `users`, `courses` и `assignments`, включая ветви
условий. Миграции и стандартные точки входа ASGI/WSGI исключены. Общий результат
ниже **90%** завершает job `Tests` с ошибкой. Это единый показатель строк и ветвей,
а не отдельное требование 90% для каждого файла или только для ветвей.

Артефакт `test-reports` хранится 7 дней и содержит созданные за запуск отчёты:

- `junit.xml` - результаты тестов;
- `coverage.xml` - покрытие в машиночитаемом формате;
- `htmlcov/index.html` - интерактивный отчёт покрытия;
- `openapi.yaml` - проверенная схема API.

Доступные отчёты загружаются и при неудачных проверках. Если установка зависимостей
или другой ранний шаг завершится ошибкой, часть отчётов может отсутствовать.
Статус запусков доступен во вкладке Actions репозитория.

Локальный запуск тех же тестов с отчётами из PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q --cov --cov-config=pyproject.toml --cov-report=term-missing --cov-report=xml:artifacts/coverage.xml --cov-report=html:artifacts/htmlcov --junitxml=artifacts/junit.xml
```

Папка `artifacts/`, данные coverage и собранная статика исключены из Git и
контекста Docker-сборки. Обычный `pytest -q` остаётся доступен без измерения покрытия.

## Обновление зависимостей

```powershell
.\.venv\Scripts\pip-compile.exe --no-header --no-annotate --strip-extras --output-file requirements.txt requirements.in
.\.venv\Scripts\pip-compile.exe --no-header --no-annotate --strip-extras --output-file requirements-dev.txt requirements-dev.in
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Compose-конфигурация предназначена для разработки: включён `DEBUG`, используется
`runserver`, исходники подключены через bind mount.
