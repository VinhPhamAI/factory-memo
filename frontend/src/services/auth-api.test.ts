import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthClient, AuthError } from "./auth-api";

const user = {
  id: "0190-user",
  username: "an",
  display_name: "An",
  role: "employee",
};
const tokens = (access: string, expiresIn = 900) => ({
  access_token: access,
  token_type: "bearer",
  expires_in: expiresIn,
});
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

function stubFetch(...responses: (() => Response)[]) {
  const fetch = vi.fn(async () => {
    const next = responses.shift();
    if (!next) throw new Error("Unexpected request");
    return next();
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const requestOf = (fetch: ReturnType<typeof stubFetch>, call: number) =>
  fetch.mock.calls[call] as unknown as [string, RequestInit];

let clients: AuthClient[] = [];
const newClient = () => {
  const client = new AuthClient({ crossTab: false });
  clients.push(client);
  return client;
};

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

afterEach(() => {
  clients.forEach((client) => client.dispose());
  clients = [];
  vi.unstubAllGlobals();
});

describe("AuthClient", () => {
  it("keeps the access token in memory only and attaches it as a bearer", async () => {
    const fetch = stubFetch(
      () => json(tokens("access-1")),
      () => json(user),
    );
    const client = newClient();

    await client.login("an", "correct horse battery");

    const [url, init] = requestOf(fetch, 0);
    expect(url).toBe("/auth/login");
    expect(init.credentials).toBe("same-origin");
    expect(client.state).toEqual({ status: "signed-in", user });
    expect(
      new Headers(requestOf(fetch, 1)[1].headers).get("Authorization"),
    ).toBe("Bearer access-1");
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
    expect(document.cookie).not.toContain("access-1");
  });

  it("sends one refresh request for concurrent refresh calls in a tab", async () => {
    let release!: (response: Response) => void;
    const pending = new Promise<Response>((resolve) => (release = resolve));
    const fetch = stubFetch(
      () => pending as unknown as Response,
      () => json(user),
    );
    const client = newClient();

    const first = client.refresh();
    const second = client.refresh();
    release(json(tokens("access-2")));

    await expect(first).resolves.toBe(true);
    await expect(second).resolves.toBe(true);
    const urls = fetch.mock.calls.map(
      (call) => (call as unknown as [string])[0],
    );
    expect(urls).toEqual(["/auth/refresh", "/auth/me"]);
  });

  it("refreshes before the access token expires", async () => {
    vi.useFakeTimers();
    const fetch = stubFetch(
      () => json(tokens("access-1", 120)),
      () => json(user),
      () => json(tokens("access-2")),
    );
    const client = newClient();
    await client.login("an", "correct horse battery");

    await vi.advanceTimersByTimeAsync(119_000);

    expect(fetch).toHaveBeenCalledTimes(3);
    expect(requestOf(fetch, 2)[0]).toBe("/auth/refresh");
    expect(client.accessToken).toBe("access-2");
  });

  it("signs out after a failed refresh without retrying", async () => {
    vi.useFakeTimers();
    const fetch = stubFetch(() => json({ detail: "Not authenticated" }, 401));
    const client = newClient();

    await expect(client.refresh()).resolves.toBe(false);
    await vi.advanceTimersByTimeAsync(3_600_000);

    expect(client.state).toEqual({ status: "signed-out" });
    expect(client.accessToken).toBeNull();
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("restores a session from the refresh cookie", async () => {
    stubFetch(
      () => json(tokens("access-1")),
      () => json(user),
    );
    const client = newClient();

    await client.restore();

    expect(client.state).toEqual({ status: "signed-in", user });
  });

  it("stays signed in and reports an unconfirmed logout", async () => {
    const fetch = stubFetch(
      () => json(tokens("access-1")),
      () => json(user),
      () => {
        throw new TypeError("network down");
      },
      () => json({ detail: "Authentication is temporarily unavailable" }, 503),
      () => new Response(null, { status: 204 }),
    );
    const client = newClient();
    await client.login("an", "correct horse battery");

    await expect(client.logout()).rejects.toMatchObject({ status: 0 });
    await expect(client.logout()).rejects.toMatchObject({ status: 503 });
    expect(client.state).toEqual({ status: "signed-in", user });

    await client.logout();

    const [url, init] = requestOf(fetch, 4);
    expect(url).toBe("/auth/logout");
    expect(new Headers(init.headers).get("Authorization")).toBe(
      "Bearer access-1",
    );
    expect(client.state).toEqual({ status: "signed-out" });
    expect(client.accessToken).toBeNull();
  });

  it("shares token changes and logout with other tabs in memory", async () => {
    stubFetch(
      () => json(tokens("access-1")),
      () => json(user),
      () => new Response(null, { status: 204 }),
    );
    const first = new AuthClient();
    const second = new AuthClient();
    clients.push(first, second);
    const received = () =>
      new Promise<void>((resolve) => {
        const stop = second.subscribe(() => {
          stop();
          resolve();
        });
      });

    let change = received();
    await first.login("an", "correct horse battery");
    await change;
    expect(second.accessToken).toBe("access-1");
    expect(second.state).toEqual({ status: "signed-in", user });
    expect(localStorage.length + sessionStorage.length).toBe(0);

    change = received();
    await first.logout();
    await change;
    expect(second.state).toEqual({ status: "signed-out" });
    expect(second.accessToken).toBeNull();
  });

  it("reports signup conflicts and invalid credentials as auth errors", async () => {
    stubFetch(
      () => json({ detail: "Username is not available" }, 409),
      () => json({ detail: "Invalid username or password" }, 401),
    );
    const client = newClient();

    await expect(
      client.signup({
        username: "an",
        display_name: "An",
        password: "x".repeat(15),
      }),
    ).rejects.toMatchObject({ status: 409 });
    await expect(client.login("an", "wrong")).rejects.toBeInstanceOf(AuthError);
    expect(client.state.status).not.toBe("signed-in");
    expect(client.accessToken).toBeNull();
  });
});
