import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

const originals = Object.fromEntries(
  ["fetch", "sessionStorage", "window", "CustomEvent"].map((key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)])
);
let moduleNumber = 0;

afterEach(() => {
  for (const [key, descriptor] of Object.entries(originals)) {
    if (descriptor) Object.defineProperty(globalThis, key, descriptor);
    else delete globalThis[key];
  }
});

function response(status, body = null) {
  return new Response(body === null ? null : JSON.stringify(body), {
    status, headers: { "Content-Type": "application/json" },
  });
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

async function setup({ stored, storageFails = false } = {}) {
  const values = new Map(stored ? [["studyflow.session", JSON.stringify(stored)]] : []);
  const events = [];
  Object.defineProperty(globalThis, "sessionStorage", {
    configurable: true,
    value: {
      getItem(key) { if (storageFails) throw new Error("disabled"); return values.get(key) ?? null; },
      setItem(key, value) { if (storageFails) throw new Error("disabled"); values.set(key, value); },
      removeItem(key) { if (storageFails) throw new Error("disabled"); values.delete(key); },
    },
  });
  globalThis.CustomEvent = class { constructor(type) { this.type = type; } };
  globalThis.window = {
    location: { origin: "https://studyflow.test" }, dispatchEvent(event) { events.push(event.type); },
  };
  globalThis.fetch = async () => { throw new Error("Unexpected fetch"); };
  const client = await import(`../../users/static/studyflow/api.js?test=${++moduleNumber}`);
  return { ...client, values, events };
}

const tokens = { access: "old-access", refresh: "valid-refresh" };

test("login stores only tokens and authenticated requests send the access token", async () => {
  const client = await setup();
  const calls = [];
  globalThis.fetch = async (path, init) => {
    calls.push({ path, init });
    return response(200, path.includes("token") ? tokens : { id: 3 });
  };
  await client.login("student", "Secret-password");
  assert.equal(client.hasSession(), true);
  assert.deepEqual(JSON.parse(client.values.get("studyflow.session")), tokens);
  assert.deepEqual(await client.api("/api/users/me/"), { id: 3 });
  assert.equal(calls[0].init.headers.Authorization, undefined);
  assert.deepEqual(JSON.parse(calls[0].init.body), { username: "student", password: "Secret-password" });
  assert.equal(calls[1].init.headers.Authorization, "Bearer old-access");
  assert.equal(calls[1].init.redirect, "error");
  assert.equal(calls[1].init.method, "GET");
});

test("public requests omit authentication and 204 returns null", async () => {
  const client = await setup({ stored: tokens });
  globalThis.fetch = async (_path, init) => {
    assert.equal(init.headers.Authorization, undefined);
    assert.equal(init.headers["Content-Type"], "application/json");
    assert.equal(init.method, "POST");
    return response(204);
  };
  assert.equal(await client.api("/api/auth/register/", { method: "POST", body: {}, auth: false }), null);
});

test("DRF field errors preserve data and have readable Russian labels", async () => {
  const client = await setup();
  const data = { username: ["Уже занят."], password: ["Слишком короткий."] };
  globalThis.fetch = async () => response(400, data);
  await assert.rejects(client.api("/api/auth/register/", { auth: false }), (error) => {
    assert.ok(error instanceof client.ApiError);
    assert.equal(error.status, 400);
    assert.deepEqual(error.data, data);
    assert.match(error.message, /Логин: Уже занят/);
    assert.match(error.message, /Пароль: Слишком короткий/);
    return true;
  });
});

test("detail errors and non-JSON server responses are readable", async () => {
  const client = await setup();
  globalThis.fetch = async () => response(403, { detail: ["Доступ закрыт."] });
  await assert.rejects(client.api("/api/courses/"), { status: 403, message: "Доступ закрыт." });
  globalThis.fetch = async () => new Response("<html>Internal error</html>", { status: 503 });
  await assert.rejects(client.api("/api/courses/"), (error) => error.status === 503 && !error.message.includes("<html>"));
});

test("concurrent unauthorized calls share a single refresh and retry original bodies", async () => {
  const client = await setup({ stored: tokens });
  const release = deferred();
  const refreshStarted = deferred();
  let refreshCalls = 0;
  const attempts = [];
  globalThis.fetch = async (path, init) => {
    if (path === "/api/auth/token/refresh/") {
      refreshCalls += 1;
      assert.equal(init.headers.Authorization, undefined);
      assert.deepEqual(JSON.parse(init.body), { refresh: "valid-refresh" });
      refreshStarted.resolve();
      await release.promise;
      return response(200, { access: "new-access" });
    }
    attempts.push({ path, init });
    return init.headers.Authorization === "Bearer old-access"
      ? response(401, { detail: "Expired" }) : response(200, { path });
  };
  const first = client.api("/api/courses/1/", { method: "PATCH", body: { title: "Курс" } });
  const second = client.api("/api/courses/2/");
  await refreshStarted.promise;
  release.resolve();
  await Promise.all([first, second]);
  assert.equal(refreshCalls, 1);
  assert.equal(attempts.length, 4);
  assert.deepEqual(attempts.filter(({ path }) => path.endsWith("1/")).map(({ init }) => init.body), [JSON.stringify({ title: "Курс" }), JSON.stringify({ title: "Курс" })]);
  assert.equal(JSON.parse(client.values.get("studyflow.session")).access, "new-access");
});

test("late 401 from the old access token reuses an already refreshed token", async () => {
  const client = await setup({ stored: tokens });
  const lateResponse = deferred();
  let refreshCalls = 0;
  globalThis.fetch = async (path, init) => {
    if (path.includes("refresh")) { refreshCalls += 1; return response(200, { access: "new" }); }
    if (path.endsWith("slow/") && init.headers.Authorization === "Bearer old-access") return lateResponse.promise;
    return init.headers.Authorization === "Bearer old-access" ? response(401) : response(200, {});
  };
  const slow = client.api("/api/slow/");
  await client.api("/api/fast/");
  lateResponse.resolve(response(401));
  await slow;
  assert.equal(refreshCalls, 1);
});

test("invalid refresh clears session and emits logout once", async () => {
  const client = await setup({ stored: tokens });
  globalThis.fetch = async () => response(401, { detail: "Недействительный токен." });
  await assert.rejects(client.api("/api/courses/"), { status: 401 });
  assert.equal(client.hasSession(), false);
  assert.equal(client.values.has("studyflow.session"), false);
  assert.deepEqual(client.events, ["studyflow:logout"]);
});

test("retry is bounded and a repeated 401 ends the session", async () => {
  const client = await setup({ stored: tokens });
  let calls = 0;
  globalThis.fetch = async (path) => {
    calls += 1;
    return path.includes("refresh") ? response(200, { access: "new" }) : response(401);
  };
  await assert.rejects(client.api("/api/courses/"), { status: 401 });
  assert.equal(calls, 3);
  assert.equal(client.hasSession(), false);
  assert.deepEqual(client.events, ["studyflow:logout"]);
});

for (const failure of ["network", "server"]) {
  test(`${failure} failure during refresh preserves the existing session`, async () => {
    const client = await setup({ stored: tokens });
    globalThis.fetch = async (path) => {
      if (!path.includes("refresh")) return response(401);
      if (failure === "network") throw new TypeError("Offline");
      return response(503);
    };
    await assert.rejects(client.api("/api/courses/"), { status: failure === "network" ? 0 : 503 });
    assert.equal(client.hasSession(), true);
    assert.deepEqual(JSON.parse(client.values.get("studyflow.session")), tokens);
    assert.deepEqual(client.events, []);
  });
}

test("logout clears storage and a late refresh cannot restore it", async () => {
  const client = await setup({ stored: tokens });
  const started = deferred();
  const delayed = deferred();
  globalThis.fetch = async (path) => {
    if (path.includes("refresh")) { started.resolve(); return delayed.promise; }
    return response(401);
  };
  const pending = client.api("/api/courses/");
  const rejected = assert.rejects(pending, { status: 409 });
  await started.promise;
  client.logout();
  delayed.resolve(response(200, { access: "too-late" }));
  await rejected;
  assert.equal(client.hasSession(), false);
  assert.equal(client.values.has("studyflow.session"), false);
  assert.deepEqual(client.events, ["studyflow:logout"]);
});

test("late login cannot restore session after logout", async () => {
  const client = await setup();
  const delayed = deferred();
  globalThis.fetch = async () => delayed.promise;
  const pending = client.login("student", "password");
  const rejected = assert.rejects(pending, { status: 409 });
  client.logout();
  delayed.resolve(response(200, tokens));
  await rejected;
  assert.equal(client.hasSession(), false);
});

test("account switching prevents old refresh from overwriting the new session", async () => {
  const client = await setup({ stored: tokens });
  const delayed = deferred();
  const started = deferred();
  const newer = { access: "teacher-access", refresh: "teacher-refresh" };
  globalThis.fetch = async (path) => {
    if (path.includes("refresh")) { started.resolve(); return delayed.promise; }
    if (path === "/api/auth/token/") return response(200, newer);
    return response(401);
  };
  const pending = client.api("/api/courses/");
  const rejected = assert.rejects(pending, { status: 409 });
  await started.promise;
  await client.login("teacher", "password");
  delayed.resolve(response(200, { access: "stale" }));
  await rejected;
  assert.deepEqual(JSON.parse(client.values.get("studyflow.session")), newer);
  assert.deepEqual(client.events, []);
});

test("storage failure falls back to memory and still allows logout", async () => {
  const client = await setup({ storageFails: true });
  globalThis.fetch = async (path, init) => {
    if (path.includes("token")) return response(200, tokens);
    assert.equal(init.headers.Authorization, "Bearer old-access");
    return response(200, {});
  };
  await client.login("student", "password");
  assert.equal(client.hasSession(), true);
  await client.api("/api/users/me/");
  client.logout();
  assert.equal(client.hasSession(), false);
});

test("external and escaped non-API paths are rejected before fetch", async () => {
  const client = await setup({ stored: tokens });
  let calls = 0;
  globalThis.fetch = async () => { calls += 1; return response(200); };
  for (const path of ["https://evil.test/api/", "https://studyflow.test/api/", "//evil.test/api/", "/admin/", "/api/../admin/", "/api/%2e%2e/admin/", "/api/\\evil.test", "/api/courses/#fragment"]) {
    await assert.rejects(client.api(path), TypeError);
  }
  assert.equal(calls, 0);
});

test("failed login and malformed token responses never create a session", async () => {
  const client = await setup();
  globalThis.fetch = async () => response(401, { detail: "Неверный пароль." });
  await assert.rejects(client.login("student", "bad"), { status: 401 });
  globalThis.fetch = async () => response(200, { access: "missing-refresh" });
  await assert.rejects(client.login("student", "password"), { status: 502 });
  assert.equal(client.hasSession(), false);
  assert.deepEqual(client.events, []);
});

test("the latest login wins when account requests finish out of order", async () => {
  const client = await setup();
  const firstResponse = deferred();
  const newer = { access: "second-access", refresh: "second-refresh" };
  globalThis.fetch = async (_path, init) => JSON.parse(init.body).username === "first"
    ? firstResponse.promise : response(200, newer);
  const pending = client.login("first", "password");
  const rejected = assert.rejects(pending, { status: 409 });
  await client.login("second", "password");
  firstResponse.resolve(response(200, tokens));
  await rejected;
  assert.deepEqual(JSON.parse(client.values.get("studyflow.session")), newer);
});

test("a public 401 does not refresh or clear another active session", async () => {
  const client = await setup({ stored: tokens });
  let calls = 0;
  globalThis.fetch = async (_path, init) => {
    calls += 1;
    assert.equal(init.headers.Authorization, undefined);
    return response(401);
  };
  await assert.rejects(client.api("/api/public/", { auth: false }), { status: 401 });
  assert.equal(calls, 1);
  assert.equal(client.hasSession(), true);
  assert.deepEqual(client.events, []);
});

test("a failed refresh flight can be retried after the server recovers", async () => {
  const client = await setup({ stored: tokens });
  let refreshCalls = 0;
  globalThis.fetch = async (path, init) => {
    if (path.includes("refresh")) {
      refreshCalls += 1;
      return refreshCalls === 1 ? response(503) : response(200, { access: "recovered" });
    }
    return init.headers.Authorization === "Bearer recovered" ? response(200, {}) : response(401);
  };
  await assert.rejects(client.api("/api/courses/"), { status: 503 });
  assert.deepEqual(await client.api("/api/courses/"), {});
  assert.equal(refreshCalls, 2);
  assert.equal(client.hasSession(), true);
});

test("an old successful API response is rejected after switching accounts", async () => {
  const client = await setup({ stored: tokens });
  const delayed = deferred();
  globalThis.fetch = async (path) => path.includes("token")
    ? response(200, { access: "second", refresh: "second-refresh" }) : delayed.promise;
  const pending = client.api("/api/users/me/");
  const rejected = assert.rejects(pending, { status: 409 });
  await client.login("other", "password");
  delayed.resolve(response(200, { username: "previous" }));
  await rejected;
  assert.equal(client.hasSession(), true);
});
