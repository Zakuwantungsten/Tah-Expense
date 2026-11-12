import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { login as apiLogin, logout as apiLogout } from "../api/auth";
import { ApiError, tryRefreshSession } from "../api/client";
import { saveRemembered } from "./remember";
import { clearTokens, getRefreshToken, hasStoredSession } from "./tokens";

type AuthState = {
  ready: boolean;
  signedIn: boolean;
  username: string;
  error: string | null;
  signIn: (username: string, password: string, remember?: boolean) => Promise<void>;
  signOut: () => Promise<void>;
  clearError: () => void;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const [signedIn, setSignedIn] = useState(false);
  const [username, setUsername] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      // Prefer silent refresh so "login once" survives app restarts.
      // If the phone is offline, keep the stored session instead of wiping tokens.
      const hadRefresh = Boolean(await getRefreshToken());
      if (hadRefresh) {
        await tryRefreshSession();
      }
      const has = await hasStoredSession();
      if (alive) {
        setSignedIn(has);
        setReady(true);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const signIn = useCallback(
    async (user: string, password: string, remember = true) => {
      setError(null);
      try {
        await apiLogin(user.trim(), password);
        await saveRemembered(Boolean(remember), user.trim(), password);
        setUsername(user.trim());
        setSignedIn(true);
      } catch (exc) {
        const message =
          exc instanceof ApiError
            ? exc.message
            : "Could not sign in. Check the API URL and credentials.";
        setError(message);
        throw exc;
      }
    },
    []
  );

  const signOut = useCallback(async () => {
    await apiLogout();
    await clearTokens();
    setSignedIn(false);
    setUsername("");
  }, []);

  const value = useMemo(
    () => ({
      ready,
      signedIn,
      username,
      error,
      signIn,
      signOut,
      clearError: () => setError(null),
    }),
    [ready, signedIn, username, error, signIn, signOut]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used inside AuthProvider");
  }
  return ctx;
}
