import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { BrandMark } from "@/components/brand";
import { useAuthClient } from "@/hooks/auth";
import { AuthError } from "@/services/auth-api";

function message(error: unknown, mode: "login" | "signup"): string {
  if (!(error instanceof AuthError)) return "Đã xảy ra lỗi, vui lòng thử lại.";
  if (error.status === 401) return "Sai tên đăng nhập hoặc mật khẩu.";
  if (error.status === 409) return "Tên đăng nhập đã được sử dụng.";
  if (error.status === 422)
    return mode === "signup"
      ? "Kiểm tra lại: tên đăng nhập 3–32 ký tự (a–z, 0–9, _ hoặc -), mật khẩu 15–128 ký tự."
      : "Thông tin đăng nhập không hợp lệ.";
  if (error.status === 503) return "Dịch vụ đăng nhập tạm thời không khả dụng.";
  return error.message;
}

/** Employee sign-in and self-signup. Expert and admin accounts are provisioned separately. */
export function SignInScreen() {
  const client = useAuthClient();
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      if (mode === "signup") {
        await client.signup({ username, display_name: displayName, password });
        setMode("login");
        setPassword("");
        setNotice("Đã tạo tài khoản. Vui lòng đăng nhập.");
      } else {
        await client.login(username, password);
      }
    } catch (caught) {
      setError(message(caught, mode));
    } finally {
      setBusy(false);
    }
  }

  const signup = mode === "signup";
  return (
    <main className="flex min-h-screen items-center justify-center p-6">
      <form
        onSubmit={submit}
        className="flex w-full max-w-sm flex-col gap-3 rounded-lg border p-6"
        aria-label={signup ? "Đăng ký" : "Đăng nhập"}
      >
        <div className="flex items-center gap-2 font-semibold">
          <BrandMark small />
          <span>Factory memo</span>
        </div>
        <h1 className="text-lg font-semibold">
          {signup ? "Đăng ký" : "Đăng nhập"}
        </h1>
        <label className="flex flex-col gap-1 text-sm">
          Tên đăng nhập
          <Input
            name="username"
            autoComplete="username"
            required
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />
        </label>
        {signup && (
          <label className="flex flex-col gap-1 text-sm">
            Tên hiển thị
            <Input
              name="display_name"
              autoComplete="name"
              required
              maxLength={80}
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
            />
          </label>
        )}
        <label className="flex flex-col gap-1 text-sm">
          Mật khẩu
          <Input
            name="password"
            type="password"
            autoComplete={signup ? "new-password" : "current-password"}
            required
            minLength={signup ? 15 : undefined}
            maxLength={signup ? 128 : undefined}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        {notice && (
          <p role="status" className="text-sm">
            {notice}
          </p>
        )}
        <Button type="submit" disabled={busy}>
          {signup ? "Tạo tài khoản" : "Đăng nhập"}
        </Button>
        <Button
          type="button"
          variant="ghost"
          onClick={() => {
            setMode(signup ? "login" : "signup");
            setError("");
            setNotice("");
          }}
        >
          {signup ? "Đã có tài khoản? Đăng nhập" : "Chưa có tài khoản? Đăng ký"}
        </Button>
      </form>
    </main>
  );
}
