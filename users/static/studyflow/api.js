const STORAGE_KEY = "studyflow.session";
const FIELD_LABELS = {
  username: "Логин", password: "Пароль", email: "Почта",
  first_name: "Имя", last_name: "Фамилия", title: "Название",
  description: "Описание", due_at: "Срок сдачи", max_score: "Максимальный балл",
  score: "Балл", feedback: "Комментарий", answer: "Решение", solution_url: "Ссылка",
};

function validToken(value) {
  return typeof value === "string" && value.length > 0;
}

function loadSession() {
  try {
    const saved = JSON.parse(globalThis.sessionStorage.getItem(STORAGE_KEY));
    if (validToken(saved?.access) && validToken(saved?.refresh)) {
      return { access: saved.access, refresh: saved.refresh };
    }
  } catch {
    // Память вкладки остаётся доступной при запрете браузерного хранилища.
  }
  return null;
}

let session = loadSession();
let generation = 0;
let refreshFlight = null;

function saveSession(value) {
  session = value;
  try {
    if (value) globalThis.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(value));
    else globalThis.sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // Авторизация продолжает работать до закрытия вкладки.
  }
}

function describeErrors(value, field = "") {
  if (Array.isArray(value)) return value.flatMap((item) => describeErrors(item, field));
  if (value && typeof value === "object") {
    return Object.entries(value).flatMap(([key, item]) =>
      describeErrors(item, key === "non_field_errors" || key === "detail" ? "" : (FIELD_LABELS[key] || key))
    );
  }
  if (typeof value !== "string" || !value) return [];
  return [field ? `${field}: ${value}` : value];
}

function errorMessage(status, data) {
  const details = describeErrors(data?.detail ?? data);
  if (details.length) return details.join("\n");
  if (status === 401) return "Войдите в аккаунт, чтобы продолжить.";
  if (status === 403) return "У вас нет доступа к этому действию.";
  if (status === 404) return "Запрошенный объект не найден.";
  if (status >= 500) return "Сервер временно недоступен. Повторите попытку позже.";
  return "Не удалось выполнить запрос.";
}

export class ApiError extends Error {
  constructor(status, data = null, message = errorMessage(status, data)) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.data = data;
  }
}

function assertGeneration(expected) {
  if (expected !== generation) {
    throw new ApiError(409, null, "Сессия изменилась. Повторите действие.");
  }
}

function clearSession(notify) {
  generation += 1;
  refreshFlight = null;
  saveSession(null);
  if (notify && globalThis.window?.dispatchEvent) {
    const EventClass = globalThis.CustomEvent ?? globalThis.window.CustomEvent;
    globalThis.window.dispatchEvent(new EventClass("studyflow:logout"));
  }
}

export function logout() {
  clearSession(true);
}

export function hasSession() {
  return Boolean(session);
}

function apiPath(path) {
  const origin = globalThis.location?.origin ?? globalThis.window?.location?.origin ?? "http://localhost";
  if (typeof path !== "string" || !path.startsWith("/api/") || path.includes("\\")) {
    throw new TypeError("Разрешены только пути /api/ текущего сайта.");
  }
  const url = new URL(path, origin);
  if (url.origin !== origin || !url.pathname.startsWith("/api/") || url.hash) {
    throw new TypeError("Разрешены только пути /api/ текущего сайта.");
  }
  return url.pathname + url.search;
}

async function request(path, options, access) {
  const headers = { Accept: "application/json" };
  const init = {
    method: options.method ?? "GET", headers, credentials: "same-origin", redirect: "error",
  };
  if (access) headers.Authorization = `Bearer ${access}`;
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }
  let response;
  let text;
  try {
    response = await fetch(path, init);
    text = await response.text();
  } catch {
    throw new ApiError(0, null, "Не удалось связаться с сервером. Проверьте соединение.");
  }
  let data = null;
  if (text) {
    try { data = JSON.parse(text); } catch { /* Ответ без JSON обрабатывается по HTTP-статусу. */ }
  }
  return { status: response.status, ok: response.ok, data };
}

function unwrap(result) {
  if (!result.ok) throw new ApiError(result.status, result.data);
  return result.data;
}

async function refreshAccess(expected, rejectedAccess) {
  assertGeneration(expected);
  if (session?.access && session.access !== rejectedAccess) return session.access;
  if (refreshFlight?.generation === expected) return refreshFlight.promise;

  const refresh = session?.refresh;
  const flight = { generation: expected, promise: null };
  flight.promise = (async () => {
    const result = await request("/api/auth/token/refresh/", {
      method: "POST", body: { refresh },
    });
    assertGeneration(expected);
    if (!result.ok) {
      if ([400, 401, 403].includes(result.status)) clearSession(true);
      throw new ApiError(result.status, result.data);
    }
    if (!validToken(result.data?.access)) {
      throw new ApiError(502, null, "Сервер вернул некорректный токен авторизации.");
    }
    saveSession({
      access: result.data.access,
      refresh: validToken(result.data.refresh) ? result.data.refresh : refresh,
    });
    return session.access;
  })().finally(() => {
    if (refreshFlight === flight) refreshFlight = null;
  });
  refreshFlight = flight;
  return flight.promise;
}

export async function api(path, options = {}) {
  const target = apiPath(path);
  const authenticated = options.auth !== false;
  const expected = generation;
  const access = authenticated ? session?.access : undefined;
  let result = await request(target, options, access);
  if (!authenticated) return unwrap(result);
  assertGeneration(expected);
  if (result.status === 401 && session?.refresh) {
    const renewed = await refreshAccess(expected, access);
    assertGeneration(expected);
    result = await request(target, options, renewed);
    assertGeneration(expected);
    if (result.status === 401) clearSession(true);
  }
  return unwrap(result);
}

export async function login(username, password) {
  clearSession(false);
  const expected = generation;
  const data = await api("/api/auth/token/", {
    method: "POST", body: { username, password }, auth: false,
  });
  assertGeneration(expected);
  if (!validToken(data?.access) || !validToken(data?.refresh)) {
    throw new ApiError(502, null, "Сервер вернул некорректные данные авторизации.");
  }
  saveSession({ access: data.access, refresh: data.refresh });
  return data;
}
