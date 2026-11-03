import { apiBaseUrl } from "./config";

/** Public account fields returned by /auth/signup and /auth/me. */
export type AuthUser = {
  id: string;
  username: string;
  display_name: string;
  role: string;
};

export type AuthState =
  | { status: "loading" }
  | { status: "signed-out" }
  | { status: "signed-in"; user: AuthUser };

export type SignupInput = {
  username: string;
  display_name: string;
  password: string;
};

/** A non-2xx auth response; `status` is the HTTP status (0 for network errors). */
export class AuthError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "AuthError";
  }
}

type TabMessage =
  | { type: "tokens"; accessToken: string; expiresAt: number; user: AuthUser }
  | { type: "signed-out" };

const CHANNEL_NAME = "factory-memo-auth";
const LOCK_NAME = "factory-memo-auth";
// Refresh this long before access expiry, or halfway for shorter lifetimes.
const REFRESH_MARGIN_MS = 60_000;

const object = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

function parseUser(value: unknown): AuthUser {
  if (
    !object(value) ||
    typeof value.id !== "string" ||
    typeof value.username !== "string" ||
    typeof value.display_name !== "string" ||
    typeof value.role !== "string"
  )
    throw new AuthError("Thông tin tài khoản không hợp lệ.", 0);
  return {
    id: value.id,
    username: value.username,
    display_name: value.display_name,
    role: value.role,
  };
}

function parseTokens(value: unknown): {
  accessToken: string;
  expiresIn: number;
} {
  if (
    !object(value) ||
    typeof value.access_token !== "string" ||
    typeof value.expires_in !== "number"
  )
    throw new AuthError("Phản hồi đăng nhập không hợp lệ.", 0);
  return { accessToken: value.access_token, expiresIn: value.expires_in };
}

/**
 * Browser session client. The access token lives only in memory; the refresh
 * token is an HttpOnly cookie the browser sends to /auth. Refresh and logout
 * run under a Web Lock shared by same-origin tabs where supported, and token
 * changes are broadcast to other open tabs in memory, never stored.
 */
export class AuthClient {
  state: AuthState = { status: "loading" };
  accessToken: string | null = null;

  private readonly baseUrl: string;
  private readonly channel: BroadcastChannel | null;
  private readonly listeners = new Set<() => void>();
  private timer: ReturnType<typeof setTimeout> | undefined;
  private inFlight: Promise<boolean> | null = null;
  // Increases whenever tokens change, so a waiting refresh can see that
  // another tab already refreshed.
  private generation = 0;

  constructor({ crossTab = true, baseUrl = apiBaseUrl } = {}) {
    this.baseUrl = baseUrl;
    this.channel =
      crossTab && typeof BroadcastChannel !== "undefined"
        ? new BroadcastChannel(CHANNEL_NAME)
        : null;
    this.channel?.addEventListener("message", (event: MessageEvent) =>
      this.receive(event.data as TabMessage),
    );
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  dispose(): void {
    clearTimeout(this.timer);
    this.channel?.close();
    this.listeners.clear();
  }

  /** Create an employee account. Does not sign in. */
  async signup(input: SignupInput): Promise<AuthUser> {
    const response = await this.post("/auth/signup", input);
    return parseUser(await response.json());
  }

  async login(username: string, password: string): Promise<void> {
    const response = await this.post("/auth/login", { username, password });
    const { accessToken, expiresIn } = parseTokens(await response.json());
    const user = await this.me(accessToken);
    this.applyTokens(accessToken, Date.now() + expiresIn * 1000, user, true);
  }

  /** Restore identity after a page load from the refresh cookie, if any. */
  async restore(): Promise<void> {
    await this.refresh();
  }

  /**
   * Rotate the session once per tab, however many callers ask at once.
   *
   * Returns false and signs this tab out when the refresh is rejected. Another
   * tab may still be signed in: a rejected refresh never clears the cookie.
   */
  refresh(): Promise<boolean> {
    if (!this.inFlight) {
      const seen = this.generation;
      this.inFlight = this.withLock(() => this.refreshNow(seen)).finally(() => {
        this.inFlight = null;
      });
    }
    return this.inFlight;
  }

  /**
   * Revoke this session on the server and sign out every open tab.
   *
   * Throws AuthError and stays signed in when the server did not confirm the
   * logout: the session and refresh cookie would otherwise survive unseen and
   * sign the next page load back in.
   */
  async logout(): Promise<void> {
    // A refresh already in flight would otherwise rotate the session first.
    await this.inFlight;
    const status = await this.withLock(async () => {
      try {
        const response = await fetch(`${this.baseUrl}/auth/logout`, {
          method: "POST",
          credentials: "same-origin",
          headers: this.bearer(),
        });
        return response.status;
      } catch {
        return 0;
      }
    });
    if (status !== 204)
      throw new AuthError("Không đăng xuất được, vui lòng thử lại.", status);
    this.signOut();
    this.channel?.postMessage({ type: "signed-out" } satisfies TabMessage);
  }

  /** Fetch a protected route with the current access token. Never retries. */
  authorizedFetch(input: string, init: RequestInit = {}): Promise<Response> {
    const headers = new Headers(init.headers);
    if (this.accessToken)
      headers.set("Authorization", `Bearer ${this.accessToken}`);
    return fetch(input, { ...init, headers, credentials: "same-origin" });
  }

  private async refreshNow(seen: number): Promise<boolean> {
    if (this.generation !== seen && this.accessToken) return true;
    try {
      const response = await fetch(`${this.baseUrl}/auth/refresh`, {
        method: "POST",
        credentials: "same-origin",
      });
      if (!response.ok)
        throw new AuthError("Phiên đăng nhập đã hết hạn.", response.status);
      const { accessToken, expiresIn } = parseTokens(await response.json());
      const user =
        this.state.status === "signed-in"
          ? this.state.user
          : await this.me(accessToken);
      this.applyTokens(accessToken, Date.now() + expiresIn * 1000, user, true);
      return true;
    } catch {
      this.signOut();
      return false;
    }
  }

  private async me(accessToken: string): Promise<AuthUser> {
    const response = await fetch(`${this.baseUrl}/auth/me`, {
      credentials: "same-origin",
      headers: { Authorization: `Bearer ${accessToken}` },
    });
    if (!response.ok)
      throw new AuthError("Không tải được tài khoản.", response.status);
    return parseUser(await response.json());
  }

  private async post(path: string, body: unknown): Promise<Response> {
    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}${path}`, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } catch {
      throw new AuthError("Không kết nối được máy chủ.", 0);
    }
    if (!response.ok) {
      const detail: unknown = await response.json().catch(() => null);
      const message =
        object(detail) && typeof detail.detail === "string"
          ? detail.detail
          : `HTTP ${response.status}`;
      throw new AuthError(message, response.status);
    }
    return response;
  }

  private bearer(): Record<string, string> {
    return this.accessToken
      ? { Authorization: `Bearer ${this.accessToken}` }
      : {};
  }

  private withLock<T>(task: () => Promise<T>): Promise<T> {
    const locks =
      typeof navigator !== "undefined" ? navigator.locks : undefined;
    return locks ? locks.request(LOCK_NAME, task) : task();
  }

  private receive(message: TabMessage): void {
    if (message.type === "tokens")
      this.applyTokens(
        message.accessToken,
        message.expiresAt,
        message.user,
        false,
      );
    else this.signOut();
  }

  private applyTokens(
    accessToken: string,
    expiresAt: number,
    user: AuthUser,
    broadcast: boolean,
  ): void {
    this.accessToken = accessToken;
    this.generation += 1;
    this.state = { status: "signed-in", user };
    clearTimeout(this.timer);
    const lifetime = Math.max(0, expiresAt - Date.now());
    const delay = Math.max(
      0,
      lifetime - Math.min(REFRESH_MARGIN_MS, lifetime / 2),
    );
    this.timer = setTimeout(() => void this.refresh(), delay);
    if (broadcast)
      this.channel?.postMessage({
        type: "tokens",
        accessToken,
        expiresAt,
        user,
      } satisfies TabMessage);
    this.notify();
  }

  private signOut(): void {
    clearTimeout(this.timer);
    this.accessToken = null;
    this.generation += 1;
    this.state = { status: "signed-out" };
    this.notify();
  }

  private notify(): void {
    this.listeners.forEach((listener) => listener());
  }
}
