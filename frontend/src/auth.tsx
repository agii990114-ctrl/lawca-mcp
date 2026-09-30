import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { ApiError, get, post, setCsrf } from "./api";
import type { Me } from "./types";

interface AuthState {
  me: Me | null;
  loading: boolean;
  can: (permission: string) => boolean;
  login: (userId: string, password: string) => Promise<Me>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const m = await get<Me>("/auth/me");
      if (m.csrf_token) setCsrf(m.csrf_token);
      setMe(m);
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) console.error(e);
      setMe(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const onUnauthorized = () => setMe(null);      // 세션 만료 등: 로그인 화면으로
    window.addEventListener("lawca:unauthorized", onUnauthorized);
    return () => window.removeEventListener("lawca:unauthorized", onUnauthorized);
  }, [refresh]);

  const value = useMemo<AuthState>(() => ({
    me,
    loading,
    can: (p) => !!me && me.permissions.includes(p),
    login: async (userId, password) => {
      const r = await post<{ user: Me; csrf_token: string }>("/auth/login", { user_id: userId, password });
      setCsrf(r.csrf_token);
      setMe(r.user);
      return r.user;
    },
    logout: async () => {
      try {
        await post("/auth/logout");
      } finally {
        setCsrf("");
        setMe(null);
      }
    },
    refresh,
  }), [me, loading, refresh]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("AuthProvider 밖에서 useAuth를 사용했습니다.");
  return v;
}
