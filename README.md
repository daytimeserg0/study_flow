# StudyFlow

[![CI](https://github.com/daytimeserg0/study_flow/actions/workflows/ci.yml/badge.svg)](https://github.com/daytimeserg0/study_flow/actions/workflows/ci.yml)

REST API платформы учебных заданий. Преподаватель создаёт курс, зачисляет студентов,
публикует задания и проверяет решения. Студент сдаёт работы, получает обратную
связь и видит свой прогресс.

## Возможности

- Регистрация студентов, JWT, роли и доступ только к своим курсам и работам.
- Черновики и публикация заданий, дедлайны, решения текстом или ссылкой.
- Оценки и комментарии преподавателя; проверенные ответы защищены от изменений.
- Прогресс по курсу, поиск, фильтры, сортировка и пагинация.
- Транзакции и ограничения PostgreSQL для повторных отправок и конкурентных изменений.
- Swagger, демонстрационные данные, проверки прав и конкурентных запросов, CI.

## Стек

Python 3.13 · Django 5.2 LTS · Django REST Framework · PostgreSQL 18 · Simple JWT ·
drf-spectacular · pytest/pytest-django/pytest-cov · Ruff · Docker Compose · GitHub Actions.

Зависимости зафиксированы в `requirements.txt` и `requirements-dev.txt`.

## Быстрый запуск

Нужны Git и Docker Compose с Linux-контейнерами. Команды для PowerShell:

```powershell
git clone https://github.com/daytimeserg0/study_flow.git
cd study_flow
Copy-Item .env.example .env
docker compose up --build --wait
docker compose exec web python manage.py seed_demo
```

Для существующего проекта сохраняется текущий `.env`. Compose ждёт PostgreSQL,
применяет миграции и запускает приложение. Данные БД хранятся в Docker-томе;
`docker compose down` останавливает сервисы и сохраняет данные.

| Адрес | Назначение |
| --- | --- |
| [Swagger](http://127.0.0.1:8000/api/docs/) | Документация и запросы к API |
| [OpenAPI](http://127.0.0.1:8000/api/schema/) | Схема API |
| [Django Admin](http://127.0.0.1:8000/admin/) | Администрирование |
| [Health](http://127.0.0.1:8000/api/health/) | Состояние приложения и БД |

Для входа в админку создаётся отдельный суперпользователь:

```powershell
docker compose exec web python manage.py createsuperuser
```

Запуск без Docker описан в [руководстве разработчика](docs/development.md).

## Демонстрация

`seed_demo` создаёт двух преподавателей, двух студентов, два курса и задания
с открытыми и истёкшими сроками, черновиком, решениями и оценками.

| Username | Роль |
| --- | --- |
| `demo_teacher` | Преподаватель курса Python |
| `demo_other_teacher` | Преподаватель курса баз данных |
| `demo_student` | Студент курса Python |
| `demo_peer` | Студент обоих курсов |

Пароль новых демопользователей по умолчанию: `StudyFlow-demo-2026!`.
Команда доступна только при `DJANGO_DEBUG=True`; демонстрационные аккаунты
не имеют прав администратора. Повторный запуск создаёт недостающие демообъекты,
сохраняя существующие пароли, решения и оценки.

Полный цикл через настоящий HTTP API можно проверить одной командой:

```powershell
docker compose exec web python scripts/demo_api.py
```

Сценарий создаёт отдельный курс, зачисляет студента, публикует задание, отправляет
решение, выставляет оценку и проверяет прогресс и ограничения доступа.
Созданные данные сохраняются. Пошаговый сценарий Swagger — в [демонстрации](docs/demo.md).

## Проверки

```powershell
docker compose exec web python -m pytest -q --cov --cov-config=pyproject.toml
docker compose exec web python -m ruff check .
docker compose exec web python -m ruff format --check .
```

GitHub Actions запускает `Lint`, `Tests` и `Docker image` при push в `main` и PR
в эту ветку. CI использует PostgreSQL 18, проверяет миграции и OpenAPI, собирает
статику, запускает тесты и собирает Docker-образ. Порог общего покрытия строк
и ветвей — 90%. JUnit, HTML/XML покрытия и схема API доступны в артефактах запуска.

[Все команды и устройство CI](docs/development.md#ci).

## Документация

| Документ | Содержание |
| --- | --- |
| [API](docs/api.md) | Маршруты, параметры, права и ответы |
| [Демонстрация](docs/demo.md) | Подготовка данных и сценарий проверки |
| [Архитектура](docs/architecture.md) | Модель данных, транзакции и принятые решения |
| [Разработка](docs/development.md) | Локальная среда, тесты, CI и зависимости |
| [Описание для резюме](docs/resume.md) | Краткое описание проекта и темы для собеседования |

## Структура

```text
config/          настройки, маршруты, пагинация и health endpoint
users/           пользователи, роли и JWT
courses/         курсы, зачисления, прогресс и seed_demo
assignments/     задания, решения, оценки и фильтры
scripts/         сценарий демонстрации через HTTP API
tests/           проверки API, прав, транзакций и SQL-запросов
docs/            документация
.github/         CI
```

Проект представляет собой backend API, без отдельного frontend. Docker Compose
предназначен для разработки и использует `runserver`. Загрузка файлов, история
попыток и оценок, уведомления и ИИ пока не реализованы. Возможное добавление ИИ
описано в [архитектуре](docs/architecture.md).
