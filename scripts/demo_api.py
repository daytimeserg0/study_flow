import argparse
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


class DemoError(Exception):
    pass


class Client:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")

    def request(self, method, path, *, data=None, token=None, expected=200):
        headers = {"Accept": "application/json"}
        body = None
        if data is not None:
            body = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=15) as response:
                code, content = response.status, response.read()
        except HTTPError as error:
            code, content = error.code, error.read()
        if code != expected:
            raise DemoError(f"{method} {path}: ожидался HTTP {expected}, получен {code}.")
        return json.loads(content) if content else None

    def login(self, username, password):
        tokens = self.request(
            "POST", "/api/auth/token/", data={"username": username, "password": password}
        )
        return tokens["access"]


def run_demo(client, password):
    client.request("GET", "/api/health/")
    teacher = client.login("demo_teacher", password)
    student = client.login("demo_student", password)
    peer = client.login("demo_peer", password)
    print("Вход преподавателя и двух студентов: OK")

    course = client.request(
        "POST",
        "/api/courses/",
        token=teacher,
        data={
            "title": f"[Demo API] Python {uuid4().hex[:8]}",
            "description": "Проверка полного цикла",
        },
        expected=201,
    )
    course_url = f"/api/courses/{course['id']}/"
    client.request("GET", course_url, token=student, expected=404)
    client.request(
        "POST",
        course_url + "students/",
        token=teacher,
        data={"username": "demo_student"},
        expected=201,
    )
    client.request("GET", course_url, token=student)
    client.request("GET", course_url + "students/", token=student, expected=403)
    client.request(
        "POST",
        course_url + "students/",
        token=teacher,
        data={"username": "demo_peer"},
        expected=201,
    )
    print("Курс и зачисление, ограничения доступа: OK")

    assignment = client.request(
        "POST",
        course_url + "assignments/",
        token=teacher,
        data={
            "title": "Сумма чисел",
            "description": "Напишите функцию суммы чисел.",
            "max_score": 50,
        },
        expected=201,
    )
    assignment_url = course_url + f"assignments/{assignment['id']}/"
    client.request("GET", assignment_url, token=student, expected=404)
    client.request("POST", assignment_url + "publish/", token=teacher)
    client.request("GET", assignment_url, token=student)
    client.request("GET", assignment_url, token=peer)
    print("Черновик скрыт от студента, публикация открывает доступ: OK")

    submission = client.request(
        "POST",
        assignment_url + "submissions/",
        token=student,
        data={"answer": "def total(numbers):\n    return sum(numbers)\n"},
        expected=201,
    )
    submission_url = assignment_url + f"submissions/{submission['id']}/"
    client.request(
        "POST",
        assignment_url + "submissions/",
        token=student,
        data={"answer": "Повторная отправка"},
        expected=409,
    )
    client.request("GET", submission_url, token=peer, expected=404)
    client.request(
        "POST",
        submission_url + "grade/",
        token=teacher,
        data={"score": 45, "feedback": "Решение верное. Добавьте тест пустой последовательности."},
    )
    checked = client.request("GET", submission_url, token=student)
    if checked["status"] != "graded" or checked["grade"]["score"] != 45:
        raise DemoError("Студент получил неверный результат проверки.")
    client.request(
        "PATCH",
        submission_url,
        token=student,
        data={"answer": "Подмена"},
        expected=409,
    )
    print("Сдача, приватность, оценка и запрет изменения проверенной работы: OK")

    progress = client.request("GET", course_url + "progress/", token=student)
    if progress["count"] != 1:
        raise DemoError("Сводка студента содержит лишние записи.")
    row = progress["results"][0]
    expected = {
        "assignments_count": 1,
        "submitted_count": 1,
        "graded_count": 1,
        "overdue_count": 0,
        "earned_score": 45,
        "max_score": 50,
        "completion_percent": 100.0,
    }
    if any(row[key] != value for key, value in expected.items()):
        raise DemoError("Прогресс не соответствует сданной и проверенной работе.")
    print("Прогресс: 1 из 1 заданий, оценка 45/50, завершённость 100%: OK")
    print(f"Демонстрационный курс сохранён: {client.base_url}{course_url}")


def main():
    parser = argparse.ArgumentParser(description="Проверка полного цикла StudyFlow через HTTP API.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--password", default="StudyFlow-demo-2026!")
    args = parser.parse_args()
    try:
        run_demo(Client(args.base_url), args.password)
    except (DemoError, URLError, TimeoutError, ValueError, KeyError) as error:
        parser.exit(1, f"Демонстрация не завершена: {error}\n")


if __name__ == "__main__":
    main()
