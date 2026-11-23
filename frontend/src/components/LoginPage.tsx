import { useEffect, useState, type FormEvent } from "react";
import { api, errorMessage } from "../api.ts";
import type { AuthConfig, AuthResponse } from "../types.ts";

type Mode = "login" | "register";

interface LoginPageProps {
  onAuthenticated: (auth: AuthResponse) => void;
  notice?: string;
}

export default function LoginPage({ onAuthenticated, notice }: LoginPageProps) {
  const [mode, setMode] = useState<Mode>("login");
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .authConfig()
      .then(setConfig)
      .catch((e: unknown) => setError(`Không kết nối được backend: ${errorMessage(e)}`));
  }, []);

  const minLength = config?.min_password_length ?? 8;

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError("");
    if (mode === "register") {
      if (password.length < minLength) return setError(`Mật khẩu tối thiểu ${minLength} ký tự.`);
      if (password !== confirm) return setError("Mật khẩu nhập lại không khớp.");
    }
    setBusy(true);
    try {
      const auth =
        mode === "login"
          ? await api.login(username, password)
          : await api.register(username, password, displayName);
      onAuthenticated(auth);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  function switchMode(next: Mode) {
    setMode(next);
    setError("");
    setPassword("");
    setConfirm("");
  }

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={onSubmit}>
        <h1>Tra cứu tài liệu</h1>
        {config?.allow_registration && (
          <div className="tabs login-tabs">
            <button type="button" className={mode === "login" ? "active" : ""} onClick={() => switchMode("login")}>
              Đăng nhập
            </button>
            <button
              type="button"
              className={mode === "register" ? "active" : ""}
              onClick={() => switchMode("register")}
            >
              Đăng ký
            </button>
          </div>
        )}
        {notice && !error && <div className="notice">{notice}</div>}
        <label>
          Tên đăng nhập
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            autoFocus
            required
            minLength={3}
            maxLength={40}
          />
        </label>
        {mode === "register" && (
          <label>
            Tên hiển thị
            <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} maxLength={80} />
          </label>
        )}
        <label>
          Mật khẩu
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            required
          />
        </label>
        {mode === "register" && (
          <label>
            Nhập lại mật khẩu
            <input
              type="password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              autoComplete="new-password"
              required
            />
          </label>
        )}
        {mode === "register" && (
          <p className="hint">
            Tên đăng nhập: chữ thường không dấu, số, dấu chấm, gạch dưới. Mật khẩu tối thiểu {minLength} ký tự.
          </p>
        )}
        {error && <div className="error">{error}</div>}
        <button type="submit" className="primary-button wide" disabled={busy}>
          {busy ? "Đang xử lý…" : mode === "login" ? "Đăng nhập" : "Tạo tài khoản"}
        </button>
      </form>
    </div>
  );
}
