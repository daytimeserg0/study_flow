import { api, login, logout, hasSession } from "./api.js";

const $ = (selector) => document.querySelector(selector);
const page = $("#page");
const modal = $("#modal");
let user = null;
let current = null;
let renderVersion = 0;
let registration = false;
let modalAction = null;
let modalBusy = false;
let toastTimer;

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[char]);
const teacher = () => user?.role === "teacher";
const name = (person) => [person.first_name, person.last_name].filter(Boolean).join(" ") || person.username;
const date = (value) => value ? new Intl.DateTimeFormat("ru-RU", {
  day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
}).format(new Date(value)) : "Без срока сдачи";
const coursePath = (id) => `/api/courses/${id}/`;
const assignmentPath = (course, id) => `${coursePath(course)}assignments/${id}/`;
const closed = (assignment) => assignment.due_at && new Date(assignment.due_at) <= new Date();
const action = (id, text, style = "secondary", extra = "") =>
  `<button type="button" class="button ${style}" data-action="${id}" ${extra}>${text}</button>`;
const badge = (status) => `<span class="badge ${esc(status)}">${({
  draft: "Черновик", published: "Опубликовано", submitted: "На проверке", graded: "Проверено",
})[status] || esc(status)}</span>`;
const empty = (title, text) => `<div class="empty-state"><h3>${esc(title)}</h3><p>${esc(text)}</p></div>`;

function route() {
  const [path = "courses", query = ""] = location.hash.slice(1).split("?");
  const match = path.match(/^courses\/(\d+)(?:\/assignments\/(\d+))?$/);
  return { path, params: new URLSearchParams(query), courseId: match?.[1], assignmentId: match?.[2] };
}

function href(params = {}) {
  const currentRoute = route();
  Object.entries(params).forEach(([key, value]) => {
    if (value == null || value === "") currentRoute.params.delete(key);
    else currentRoute.params.set(key, String(value));
  });
  const query = currentRoute.params.toString();
  return `#${currentRoute.path || "courses"}${query ? `?${query}` : ""}`;
}

function pagination(data) {
  if (!data.count) return "";
  const number = Number(route().params.get("page")) || 1;
  const pages = Math.ceil(data.count / 12);
  return `<nav class="pagination" aria-label="Страницы списка"><span class="muted">Всего: ${data.count} · Страница ${number} из ${pages}</span><div class="actions">
    ${data.previous ? `<a class="button secondary small" href="${esc(href({ page: number - 1 }))}">Назад</a>` : ""}
    ${data.next ? `<a class="button secondary small" href="${esc(href({ page: number + 1 }))}">Далее</a>` : ""}
  </div></nav>`;
}

function listQuery(params, allowed = []) {
  const query = new URLSearchParams({ page_size: "12" });
  for (const key of ["page", ...allowed]) if (params.has(key)) query.set(key, params.get(key));
  return `?${query}`;
}

function searchToolbar(params, kind) {
  const statuses = kind === "assignments"
    ? (teacher() ? [["draft", "Черновики"], ["published", "Опубликованные"]] : [])
    : kind === "submissions" ? [["submitted", "На проверке"], ["graded", "Проверенные"]] : [];
  return `<form class="toolbar" id="search-form"><div class="field"><label for="search">Поиск ${kind === "courses" ? "курсов" : kind === "assignments" ? "заданий" : "по студенту"}</label>
    <input type="search" id="search" name="search" value="${esc(params.get("search"))}" placeholder="${kind === "submissions" ? "Логин или имя" : "Название или описание"}"></div>
    ${statuses.length ? `<div class="field"><label for="status">Состояние</label><select id="status" name="status"><option value="">Все</option>${statuses.map(([value, label]) => `<option value="${value}" ${params.get("status") === value ? "selected" : ""}>${label}</option>`).join("")}</select></div>` : ""}
    <button class="button secondary" type="submit">Найти</button>
    ${params.get("search") || params.get("status") ? `<a class="button secondary" href="${esc(href({ search: null, status: null, page: null }))}">Сбросить</a>` : ""}</form>`;
}

function notify(message) {
  clearTimeout(toastTimer);
  $("#toast").textContent = message;
  $("#toast").hidden = false;
  toastTimer = setTimeout(() => { $("#toast").hidden = true; }, 4500);
}

function showLogin() {
  ++renderVersion;
  user = null;
  current = null;
  page.replaceChildren();
  $("#app-shell").hidden = true;
  $("#auth-screen").hidden = false;
  $("#sidebar-user").replaceChildren();
  clearTimeout(toastTimer);
  $("#toast").hidden = true;
  $("#auth-form").reset();
  if (modal.open) modal.close();
  $("#modal-body").replaceChildren();
  modalAction = null;
  document.title = "StudyFlow - вход";
}

async function enter() {
  user = await api("/api/users/me/");
  $("#auth-screen").hidden = true;
  $("#app-shell").hidden = false;
  $("#sidebar-user").innerHTML = `<strong>${esc(name(user))}</strong><span>${teacher() ? "Преподаватель" : "Студент"}</span>`;
  await render();
}

function setRegistration(value) {
  registration = value;
  document.querySelectorAll(".registration-field").forEach((field) => {
    field.hidden = !value;
    field.querySelectorAll("input").forEach((input) => { input.disabled = !value; });
  });
  $("#auth-form").elements.email.required = value;
  $("#auth-form").elements.password.autocomplete = value ? "new-password" : "current-password";
  $("#auth-title").textContent = value ? "Начнём учиться" : "С возвращением";
  $("#auth-description").textContent = value ? "Создайте аккаунт студента. Преподаватель сможет добавить вас на курс по логину." : "Войдите, чтобы продолжить работу с вашими курсами.";
  $("#auth-submit").textContent = value ? "Создать аккаунт" : "Войти";
  $("#auth-switch").textContent = value ? "Уже есть аккаунт? Войти" : "Нет аккаунта? Зарегистрироваться";
  $("#auth-error").hidden = true;
}

$("#auth-switch").addEventListener("click", () => setRegistration(!registration));
$("#auth-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const values = Object.fromEntries(new FormData(form));
  const controls = [...form.querySelectorAll("button"), ...document.querySelectorAll("[data-demo]")];
  controls.forEach((button) => { button.disabled = true; });
  $("#auth-error").hidden = true;
  try {
    if (registration) {
      await api("/api/auth/register/", { method: "POST", body: values, auth: false });
      setRegistration(false);
      notify("Аккаунт создан. Теперь можно войти.");
    }
    await login(values.username, values.password);
    form.elements.password.value = "";
    await enter();
  } catch (error) {
    $("#auth-error").textContent = error.message;
    $("#auth-error").hidden = false;
  } finally {
    controls.forEach((button) => { button.disabled = false; });
  }
});

document.querySelectorAll("[data-demo]").forEach((button) => {
  button.addEventListener("click", () => {
    setRegistration(false);
    $("#auth-form").elements.username.value = button.dataset.demo === "teacher" ? "demo_teacher" : "demo_student";
    $("#auth-form").elements.password.value = "StudyFlow-demo-2026!";
    $("#auth-form").requestSubmit();
  });
});

$("#logout").addEventListener("click", () => { logout(); showLogin(); location.hash = "courses"; });
window.addEventListener("studyflow:logout", showLogin);
window.addEventListener("hashchange", () => { if (user) render(); });

async function render() {
  const version = ++renderVersion;
  const target = route();
  current = null;
  document.querySelectorAll(".nav-link").forEach((link) => {
    const active = (target.path === "account") === (link.hash === "#account");
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  page.setAttribute("aria-busy", "true");
  page.innerHTML = '<div class="empty-state" role="status"><span class="spinner" aria-hidden="true"></span><p>Загружаем данные…</p></div>';
  try {
    let html;
    let data;
    if (target.path === "account") {
      data = {};
      html = profilePage();
    } else if (target.assignmentId) {
      const [course, assignment, submissions] = await Promise.all([
        api(coursePath(target.courseId)), api(assignmentPath(target.courseId, target.assignmentId)),
        api(`${assignmentPath(target.courseId, target.assignmentId)}submissions/${listQuery(target.params, teacher() ? ["search", "status"] : [])}`),
      ]);
      data = { course, assignment, submissions };
      html = assignmentPage(data, target);
    } else if (target.courseId) {
      const tab = ["progress", ...(teacher() ? ["students"] : [])].includes(target.params.get("tab")) ? target.params.get("tab") : "assignments";
      const [course, list] = await Promise.all([
        api(coursePath(target.courseId)),
        api(`${coursePath(target.courseId)}${tab}/${listQuery(target.params, tab === "assignments" ? ["search", "status"] : [])}`),
      ]);
      data = { course, list, tab };
      html = coursePage(data, target);
    } else {
      const courses = await api(`/api/courses/${listQuery(target.params, ["search"])}`);
      data = { courses };
      html = coursesPage(courses, target);
    }
    if (version !== renderVersion || !user) return;
    current = data;
    page.innerHTML = html;
    document.title = `${data.assignment?.title || data.course?.title || (target.path === "account" ? "Мой профиль" : "Мои курсы")} - StudyFlow`;
    window.scrollTo({ top: 0, behavior: "instant" });
  } catch (error) {
    if (version !== renderVersion || !user) return;
    page.innerHTML = `<div class="error-state"><h1>${error.status === 404 ? "Страница недоступна" : "Не удалось загрузить данные"}</h1><p>${esc(error.message)}</p><div class="actions">${action("retry", "Попробовать снова")}<a href="#courses" class="button primary">Мои курсы</a></div></div>`;
  } finally {
    if (version === renderVersion) page.removeAttribute("aria-busy");
  }
}

function coursesPage(courses, target) {
  return `<header class="page-heading"><div><p class="eyebrow">${teacher() ? "Кабинет преподавателя" : "Кабинет студента"}</p><h1>Мои курсы</h1><p class="lead">${teacher() ? "Делитесь знаниями, задавайте направление и следите за успехами группы." : "Учитесь в своём темпе. Ваши задания и обратная связь всегда под рукой."}</p></div>${teacher() ? action("create-course", "+ Создать курс", "primary") : ""}</header>
    <section class="card welcome-card"><div><p class="eyebrow">Рады видеть вас, ${esc(user.first_name || user.username)}</p><h2>${teacher() ? "Каждое задание - шаг вперёд" : "Время для новых знаний"}</h2><p class="muted">${teacher() ? "Откройте курс, чтобы опубликовать задание или проверить работы." : "Выберите курс, откройте задание и отправьте своё решение."}</p></div><div class="welcome-counter"><strong>${courses.count}</strong><span>${target.params.get("search") ? "курсов найдено" : "доступных курсов"}</span></div></section>
    ${searchToolbar(target.params, "courses")}
    ${courses.results.length ? `<section class="course-grid">${courses.results.map((course, index) => `<article class="card course-card"><div class="course-card-top"><span class="course-icon" aria-hidden="true">${String((Number(target.params.get("page") || 1) - 1) * 12 + index + 1).padStart(2, "0")}</span><span class="badge published">${teacher() ? "Мой курс" : "Вы участник"}</span></div><h2><a href="#courses/${course.id}">${esc(course.title)}</a></h2><p class="course-description muted">${esc(course.description || "У этого курса пока нет описания.")}</p><footer class="card-footer"><span class="meta">${esc(name(course.teacher))}</span><a class="button secondary small" href="#courses/${course.id}">Открыть <span aria-hidden="true">→</span></a></footer></article>`).join("")}</section>` : empty(target.params.get("search") ? "Ничего не найдено" : "Здесь появятся ваши курсы", teacher() ? "Создайте первый курс и добавьте студентов по логину." : "Передайте свой логин преподавателю - он добавит вас на курс.")}
    ${pagination(courses)}`;
}

function coursePage({ course, list, tab }, target) {
  const tabs = [["assignments", "Задания"], ...(teacher() ? [["students", "Участники"]] : []), ["progress", "Прогресс"]];
  let content;
  if (tab === "assignments") {
    content = `${searchToolbar(target.params, "assignments")}${list.results.length ? `<div class="assignment-list">${list.results.map((assignment, index) => `<article class="assignment-row"><span class="assignment-number" aria-hidden="true">${String(index + 1 + (Number(target.params.get("page") || 1) - 1) * 12).padStart(2, "0")}</span><div class="assignment-main"><h3><a href="#courses/${course.id}/assignments/${assignment.id}">${esc(assignment.title)}</a></h3><div class="meta"><span>${esc(date(assignment.due_at))}</span><span>До ${assignment.max_score} баллов</span></div></div><div class="actions">${badge(assignment.status)}${closed(assignment) && assignment.status === "published" ? '<span class="badge overdue">Срок истёк</span>' : ""}<a class="button secondary small" href="#courses/${course.id}/assignments/${assignment.id}">Открыть</a></div></article>`).join("")}</div>` : empty("Заданий пока нет", teacher() ? "Добавьте задание и опубликуйте его, когда оно будет готово." : "Опубликованные преподавателем задания появятся здесь.")}`;
  } else if (tab === "students") {
    content = `<div class="toolbar"><p class="muted">Участников: ${list.count}</p>${action("enroll", "+ Добавить студента", "primary")}</div>${list.results.length ? `<div class="table-wrap"><table><thead><tr><th>Студент</th><th>Логин</th><th>Дата зачисления</th></tr></thead><tbody>${list.results.map((row) => `<tr><td>${esc(name(row.student))}</td><td>${esc(row.student.username)}</td><td>${esc(date(row.created_at))}</td></tr>`).join("")}</tbody></table></div>` : empty("Группа пока пуста", "Добавьте студентов по логину. Аккаунт студента должен быть зарегистрирован.")}`;
  } else {
    content = list.results.length ? (teacher() ? `<p class="muted">Прогресс зачисленных студентов по опубликованным заданиям.</p><div class="table-wrap"><table><thead><tr><th>Студент</th><th>Сдано</th><th>Проверено</th><th>Просрочено</th><th>Баллы</th><th>Прогресс</th></tr></thead><tbody>${list.results.map((row) => `<tr><td><strong>${esc(name(row.student))}</strong><br><small class="muted">${esc(row.student.username)}</small></td><td>${row.submitted_count} / ${row.assignments_count}</td><td>${row.graded_count}</td><td>${row.overdue_count}</td><td>${row.earned_score} / ${row.max_score}</td><td>${progressBar(row.completion_percent)}</td></tr>`).join("")}</tbody></table></div>` : studentProgress(list.results[0])) : empty("Пока нет данных", "Прогресс появится после зачисления студентов на курс.");
  }
  return `<nav class="breadcrumbs" aria-label="Путь"><a href="#courses">Мои курсы</a><span>/</span><span>Курс</span></nav><header class="page-heading"><div><p class="eyebrow">${esc(name(course.teacher))}</p><h1>${esc(course.title)}</h1><p class="lead pre-wrap">${esc(course.description)}</p></div>${teacher() ? `<div class="actions">${action("edit-course", "Изменить курс")}${action("create-assignment", "+ Задание", "primary")}</div>` : ""}</header><nav class="tabs" aria-label="Разделы курса">${tabs.map(([key, label]) => `<a class="tab ${tab === key ? "active" : ""}" ${tab === key ? 'aria-current="page"' : ""} href="#courses/${course.id}?tab=${key}">${label}</a>`).join("")}</nav>${content}${pagination(list)}`;
}

function progressBar(percent) {
  return `<div class="progress-track" role="progressbar" aria-label="Сдано заданий" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent}"><span class="progress-fill" style="width:${Math.min(100, Math.max(0, Number(percent)))}%"></span></div><small>${percent}%</small>`;
}

function studentProgress(row) {
  return `<section class="stats-grid">${[[`${row.submitted_count} / ${row.assignments_count}`, "Заданий сдано"], [row.graded_count, "Работ проверено"], [`${row.earned_score} / ${row.max_score}`, "Баллов набрано"], [row.overdue_count, "Просрочено без решения"]].map(([value, label]) => `<div class="stat-card"><span class="stat-value">${value}</span><span class="muted">${label}</span></div>`).join("")}</section><section class="card"><h2>Ваш путь по курсу</h2>${progressBar(row.completion_percent)}<p class="muted">Доля сданных заданий. Баллы учитываются отдельно, после проверки преподавателем.</p></section>`;
}

function solutionLink(url) {
  if (!url) return "";
  try {
    if (!["http:", "https:"].includes(new URL(url).protocol)) return "";
  } catch { return ""; }
  return `<p><a href="${esc(url)}" target="_blank" rel="noopener noreferrer">Открыть ссылку на решение <span aria-hidden="true">↗</span></a></p>`;
}

function submissionCard(submission, assignment) {
  return `<article class="card submission-card"><div class="page-heading"><div><h3>${teacher() ? esc(name(submission.student)) : "Ваше решение"}</h3><p class="meta">${teacher() ? `${esc(submission.student.username)} · ` : ""}${esc(date(submission.submitted_at))}</p></div>${badge(submission.status)}</div>
    ${submission.answer ? `<pre class="answer-code">${esc(submission.answer)}</pre>` : ""}${solutionLink(submission.solution_url)}
    ${submission.grade ? `<div class="feedback"><strong>${submission.grade.score} из ${assignment.max_score} баллов</strong><p class="pre-wrap">${esc(submission.grade.feedback || "Преподаватель не оставил комментарий.")}</p><small>${esc(name(submission.grade.graded_by))} · ${esc(date(submission.grade.graded_at))}</small></div>` : '<p class="muted">Работа отправлена и ожидает проверки.</p>'}
    ${teacher() ? action("grade", submission.grade ? "Изменить оценку" : "Проверить работу", "primary", `data-id="${submission.id}"`) : !submission.grade && !closed(assignment) ? action("submit", "Изменить решение") : '<p class="notice">Редактирование закрыто: работа проверена или срок сдачи истёк.</p>'}</article>`;
}

function assignmentPage({ course, assignment, submissions }, target) {
  return `<nav class="breadcrumbs" aria-label="Путь"><a href="#courses">Мои курсы</a><span>/</span><a href="#courses/${course.id}">${esc(course.title)}</a><span>/</span><span>Задание</span></nav><header class="page-heading"><div><p class="eyebrow">Практика</p><h1>${esc(assignment.title)}</h1></div>${teacher() ? `<div class="actions">${action("edit-assignment", "Изменить")}${assignment.status === "draft" ? `${action("publish", "Опубликовать", "primary")}${action("delete-assignment", "Удалить черновик", "danger")}` : ""}</div>` : ""}</header>
    <div class="detail-grid"><section class="card detail-main"><h2>Условие задания</h2><p class="pre-wrap">${esc(assignment.description)}</p></section><aside class="card detail-aside">${badge(assignment.status)}<div><p class="muted">Срок сдачи</p><strong>${esc(date(assignment.due_at))}</strong>${closed(assignment) ? '<p class="badge overdue">Срок истёк</p>' : ""}</div><div><p class="muted">Максимальная оценка</p><strong class="stat-value">${assignment.max_score} <small>баллов</small></strong></div></aside></div>
    <section class="submissions-section"><header class="page-heading"><div><h2>${teacher() ? "Работы студентов" : "Решение и обратная связь"}</h2><p class="muted">${teacher() ? `Найдено работ: ${submissions.count}` : "Можно отправить текст, ссылку или оба варианта."}</p></div></header>
    ${teacher() && assignment.status === "published" ? searchToolbar(target.params, "submissions") : ""}
    ${submissions.results.map((submission) => submissionCard(submission, assignment)).join("")}
    ${!submissions.results.length ? (teacher() ? empty("Работ пока нет", assignment.status === "draft" ? "Опубликуйте задание, чтобы студенты могли его увидеть." : "Отправленные решения появятся здесь.") : `<div class="card">${closed(assignment) ? '<h3>Срок сдачи истёк</h3><p class="muted">Отправить решение уже нельзя. Если нужно продлить срок, обратитесь к преподавателю.</p>' : `<h3>Готовы попробовать?</h3><p class="muted">До проверки и окончания срока своё решение можно редактировать.</p>${action("submit", "Отправить решение", "primary")}`}</div>`) : ""}
    ${teacher() ? pagination(submissions) : ""}</section>`;
}

function profilePage() {
  return `<header class="page-heading"><div><p class="eyebrow">Личное пространство</p><h1>Мой профиль</h1><p class="lead">Ваши данные и роль в StudyFlow.</p></div></header><section class="card profile-card"><h2>${esc(name(user))}</h2><dl>${[["Логин", user.username], ["Электронная почта", user.email || "Не указана"], ["Роль", teacher() ? "Преподаватель" : "Студент"]].map(([label, value]) => `<div><dt class="muted">${label}</dt><dd>${esc(value)}</dd></div>`).join("")}</dl>${!teacher() ? '<p class="notice">Передайте преподавателю свой логин, чтобы получить доступ к его курсу.</p>' : ""}</section>`;
}

function field(label, key, value = "", { type = "text", required = false, max, min, multiline = false } = {}) {
  const attributes = `id="field-${key}" name="${key}" ${required ? "required" : ""} ${max != null ? `${type === "number" ? "max" : "maxlength"}="${max}"` : ""} ${min != null ? `min="${min}"` : ""}`;
  return `<div class="field"><label for="field-${key}">${label}</label>${multiline ? `<textarea ${attributes} rows="${key === "answer" ? "10" : "5"}">${esc(value)}</textarea>` : `<input ${attributes} type="${type}" value="${esc(value)}">`}</div>`;
}

function localDate(value) {
  if (!value) return "";
  const instant = new Date(value);
  return new Date(instant.getTime() - instant.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

function openModal(title, body, button, onSubmit) {
  $("#modal-title").textContent = title;
  $("#modal-body").innerHTML = body;
  $("#modal-submit").textContent = button;
  $("#modal-error").hidden = true;
  modalAction = onSubmit;
  modal.showModal();
}

function closeModal() { if (!modalBusy) modal.close(); }
$("#modal-close").addEventListener("click", closeModal);
$("#modal-cancel").addEventListener("click", closeModal);
modal.addEventListener("cancel", (event) => { if (modalBusy) event.preventDefault(); });
$("#modal-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (modalBusy) return;
  modalBusy = true;
  $("#modal-submit").disabled = true;
  $("#modal-error").hidden = true;
  try {
    await modalAction(Object.fromEntries(new FormData(event.currentTarget)));
    modal.close();
  } catch (error) {
    $("#modal-error").textContent = error.message;
    $("#modal-error").hidden = false;
  } finally {
    modalBusy = false;
    $("#modal-submit").disabled = false;
  }
});

function go(hash) {
  if (location.hash === hash) return render();
  location.hash = hash;
}

page.addEventListener("submit", (event) => {
  if (event.target.id !== "search-form") return;
  event.preventDefault();
  const values = Object.fromEntries(new FormData(event.target));
  go(href({ search: values.search.trim(), status: values.status || null, page: null }));
});

page.addEventListener("click", (event) => {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const id = button.dataset.action;
  if (id === "retry") { render(); return; }
  if (!current) return;
  const { course, assignment, submissions } = current;
  if (id === "create-course" || id === "edit-course") {
    const edit = id === "edit-course";
    openModal(edit ? "Изменить курс" : "Новый курс", field("Название курса", "title", edit ? course.title : "", { required: true, max: 200 }) + field("Описание", "description", edit ? course.description : "", { multiline: true }), edit ? "Сохранить" : "Создать курс", async (body) => {
      const saved = await api(edit ? coursePath(course.id) : "/api/courses/", { method: edit ? "PATCH" : "POST", body });
      notify(edit ? "Курс обновлён" : "Курс создан");
      await go(`#courses/${saved.id}`);
    });
  } else if (id === "enroll") {
    openModal("Добавить студента", '<p class="muted">Студент должен быть зарегистрирован в StudyFlow.</p>' + field("Логин студента", "username", "", { required: true, max: 150 }), "Добавить", async (body) => {
      await api(`${coursePath(course.id)}students/`, { method: "POST", body });
      notify("Студент добавлен на курс");
      await render();
    });
  } else if (id === "create-assignment" || id === "edit-assignment") {
    const edit = id === "edit-assignment";
    openModal(edit ? "Изменить задание" : "Новое задание", field("Название", "title", edit ? assignment.title : "", { required: true, max: 200 }) + field("Условие задания", "description", edit ? assignment.description : "", { required: true, multiline: true }) + `<div class="form-grid">${field("Максимальный балл", "max_score", edit ? assignment.max_score : 100, { type: "number", required: true, min: 1, max: 1000 })}${field("Срок сдачи (ваше время)", "due_at", edit ? localDate(assignment.due_at) : "", { type: "datetime-local" })}</div><p class="muted">${edit ? "Изменения будут видны участникам курса." : "Задание сохранится как черновик. Опубликуйте его, когда будете готовы."}</p>`, "Сохранить", async (values) => {
      const body = { title: values.title, description: values.description, max_score: Number(values.max_score) };
      const oldDate = edit ? localDate(assignment.due_at) : "";
      if (!edit || values.due_at !== oldDate) body.due_at = values.due_at ? new Date(values.due_at).toISOString() : null;
      const saved = await api(edit ? assignmentPath(course.id, assignment.id) : `${coursePath(course.id)}assignments/`, { method: edit ? "PATCH" : "POST", body });
      notify(edit ? "Задание обновлено" : "Черновик создан");
      await go(`#courses/${course.id}/assignments/${saved.id}`);
    });
  } else if (id === "publish" || id === "delete-assignment") {
    const publish = id === "publish";
    openModal(publish ? "Опубликовать задание?" : "Удалить черновик?", `<p>${publish ? "Студенты курса увидят задание и смогут отправлять решения." : "Черновик будет удалён без возможности восстановления."}</p><p><strong>${esc(assignment.title)}</strong></p>`, publish ? "Опубликовать" : "Удалить", async () => {
      await api(`${assignmentPath(course.id, assignment.id)}${publish ? "publish/" : ""}`, { method: publish ? "POST" : "DELETE" });
      notify(publish ? "Задание опубликовано" : "Черновик удалён");
      if (publish) await render();
      else await go(`#courses/${course.id}`);
    });
  } else if (id === "submit") {
    const existing = submissions.results[0];
    openModal(existing ? "Изменить решение" : "Отправить решение", field("Текст решения", "answer", existing?.answer || "", { multiline: true, max: 20000 }) + field("Ссылка на решение", "solution_url", existing?.solution_url || "", { type: "url", max: 500 }) + '<p class="muted">Заполните хотя бы одно поле. Прикрепить файл напрямую пока нельзя.</p>', existing ? "Сохранить изменения" : "Отправить", async (body) => {
      await api(`${assignmentPath(course.id, assignment.id)}submissions/${existing ? `${existing.id}/` : ""}`, { method: existing ? "PATCH" : "POST", body });
      notify("Решение сохранено");
      await render();
    });
  } else if (id === "grade") {
    const submission = submissions.results.find((item) => item.id === Number(button.dataset.id));
    openModal("Проверка работы", `<p class="muted">${esc(name(submission.student))} · ${esc(assignment.title)}</p>` + field(`Оценка (от 0 до ${assignment.max_score})`, "score", submission.grade?.score ?? "", { type: "number", required: true, min: 0, max: assignment.max_score }) + field("Комментарий студенту", "feedback", submission.grade?.feedback || "", { multiline: true, max: 5000 }), "Сохранить оценку", async (values) => {
      await api(`${assignmentPath(course.id, assignment.id)}submissions/${submission.id}/grade/`, { method: "POST", body: { score: Number(values.score), feedback: values.feedback } });
      notify("Оценка сохранена");
      await go(href({ page: null }));
    });
  }
});

setRegistration(false);
if (hasSession()) {
  $("#auth-screen").hidden = true;
  $("#app-shell").hidden = false;
  page.innerHTML = '<div class="empty-state" role="status">Восстанавливаем вход…</div>';
  enter().catch((error) => {
    showLogin();
    $("#auth-error").textContent = error.message;
    $("#auth-error").hidden = false;
  });
}
