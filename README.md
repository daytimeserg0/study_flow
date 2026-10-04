# StudyFlow

Бэкенд платформы учебных заданий на Django и Django REST Framework.
Преподаватели будут публиковать задания и проверять работы, студенты — отправлять
решения и получать обратную связь.

В текущей версии подготовлена основа проекта: PostgreSQL, собственная модель
пользователя, Django Admin, Swagger, проверка состояния приложения и тестовое
окружение. Бизнес-логика курсов и заданий пока не реализована.

## Стек

- Python 3.13, Django 5.2 LTS, Django REST Framework.
- PostgreSQL 18, psycopg 3.
- drf-spectacular и локальные ресурсы Swagger UI.
- pytest, pytest-django, Ruff.
- Docker Compose.

Версии зависимостей зафиксированы в `requirements.txt` и `requirements-dev.txt`.
Файлы `.in` содержат исходные ограничения версий.

## Запуск через Docker

Нужны Docker Engine или Docker Desktop с поддержкой Linux-контейнеров и Docker Compose.
Команды ниже приведены для PowerShell из корня проекта.

```powershell
Copy-Item .env.example .env
docker compose up --build --wait
docker compose exec web python manage.py createsuperuser
```

`.env.example` содержит настройки только для локальной разработки. Существующий
`.env` повторно копировать не нужно. Docker Compose сначала дожидается готовности
PostgreSQL, затем применяет миграции и запускает сервер разработки Django.
Флаг `--wait` возвращает управление после успешной проверки состояния приложения.

| Адрес | Назначение |
| --- | --- |
| http://127.0.0.1:8000/api/docs/ | Swagger UI |
| http://127.0.0.1:8000/api/schema/ | Схема OpenAPI |
| http://127.0.0.1:8000/admin/ | Django Admin |
| http://127.0.0.1:8000/api/health/ | Состояние приложения и подключения к БД |

`/` перенаправляет на Swagger. Ресурсы Swagger поставляются вместе с приложением
и не требуют доступа к внешнему CDN.

PostgreSQL доступен с хоста по адресу `127.0.0.1` на порту `POSTGRES_PORT`
(по умолчанию `55432`). Внутри сети Compose приложение подключается к `db:5432`.
Данные БД сохраняются в именованном Docker-томе.

```powershell
docker compose logs -f web
docker compose down
```

`docker compose down` останавливает сервисы и сохраняет данные БД.

## Локальная разработка

Нужны Python 3.13 и PostgreSQL 18. Базу можно запустить отдельно через Compose:

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
Время хранится с учётом часового пояса; часовой пояс приложения — UTC.

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
а также ответы health endpoint при доступной и недоступной БД.

## Структура

```text
config/          настройки, маршруты, ASGI/WSGI и health endpoint
users/           собственная модель пользователя и админка
courses/         приложение курсов
assignments/     приложение заданий
tests/           проверки конфигурации и доступных endpoint
compose.yaml     сервисы приложения и PostgreSQL
```

`users.User` наследует `AbstractUser` и создаётся первой миграцией приложения.
API по умолчанию требует авторизацию; health endpoint и документация открыты.
JWT-аутентификация и роли появятся на следующем этапе.

## Обновление зависимостей

```powershell
.\.venv\Scripts\pip-compile.exe --no-header --no-annotate --strip-extras --output-file requirements.txt requirements.in
.\.venv\Scripts\pip-compile.exe --no-header --no-annotate --strip-extras --output-file requirements-dev.txt requirements-dev.in
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Compose-конфигурация предназначена для разработки: включён `DEBUG`, используется
`runserver`, исходники подключены через bind mount.
