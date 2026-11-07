import { useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import { api, ApiError, onUnauthorized, setCsrfToken } from "../api/client";
import type { LoginResult, Me } from "../api/types";

export type AuthStatus = "loading" | "anonymous" | "mfa" | "authenticated";

interface AuthValue {
  status: AuthStatus;
  me: Me | null;
  loadError: string | null;
  /** True after the person chose to sign out, as opposed to a session that ran out. */
  signedOutByUser: boolean;
  login: (organization: string, email: string, password: string) => Promise<void>;
  verifyMfa: (code: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
  can: (permission: string) => boolean;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [me, setMe] = useState<Me | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [signedOutByUser, setSignedOutByUser] = useState(false);

  const clear = useCallback(() => {
    setCsrfToken(null);
    setMe(null);
    setStatus("anonymous");
    queryClient.clear();
  }, [queryClient]);

  const loadMe = useCallback(async () => {
    try {
      const result = await api.get<Me>("/auth/me", { skipAuthRedirect: true });
      setCsrfToken(result.csrf_token ?? null);
      setMe(result);
      setStatus("authenticated");
      setLoadError(null);
      setSignedOutByUser(false);
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        clear();
        return;
      }
      setLoadError(error instanceof Error ? error.message : "Cannot reach the server.");
      setStatus("anonymous");
    }
  }, [clear]);

  useEffect(() => {
    onUnauthorized(clear);
    void loadMe();
    return () => onUnauthorized(null);
  }, [clear, loadMe]);

  const login = useCallback(
    async (organization: string, email: string, password: string) => {
      const result = await api.post<LoginResult>(
        "/auth/login",
        { organization, email, password },
        { skipAuthRedirect: true },
      );
      if (result.state === "mfa_pending") {
        setStatus("mfa");
        return;
      }
      setCsrfToken(result.csrf_token ?? null);
      await loadMe();
    },
    [loadMe],
  );

  const verifyMfa = useCallback(
    async (code: string) => {
      const result = await api.post<LoginResult>("/auth/mfa", { code }, { skipAuthRedirect: true });
      setCsrfToken(result.csrf_token ?? null);
      await loadMe();
    },
    [loadMe],
  );

  const logout = useCallback(async () => {
    setSignedOutByUser(true);
    try {
      await api.post("/auth/logout", undefined, { skipAuthRedirect: true });
    } finally {
      clear();
    }
  }, [clear]);

  const value = useMemo<AuthValue>(
    () => ({
      status,
      me,
      loadError,
      signedOutByUser,
      login,
      verifyMfa,
      logout,
      refresh: loadMe,
      can: (permission) => me?.system_permissions.includes(permission) ?? false,
    }),
    [status, me, loadError, signedOutByUser, login, verifyMfa, logout, loadMe],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
