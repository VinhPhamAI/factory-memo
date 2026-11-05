import {
  createContext,
  useContext,
  useEffect,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import {
  AuthClient,
  sessionClient,
  type AuthState,
} from "@/services/auth-api";
import { SignInScreen } from "@/components/sign-in";

const Context = createContext<AuthClient | null>(null);

/** Owns one in-memory auth session per tab and restores it on page load. */
export function AuthProvider({
  children,
  client: injected,
}: {
  children: ReactNode;
  client?: AuthClient;
}) {
  const [client] = useState(() => injected ?? sessionClient);
  useEffect(() => {
    // The client lives as long as the tab; StrictMode's second call joins the
    // first refresh instead of sending another.
    void client.restore();
  }, [client]);
  return <Context.Provider value={client}>{children}</Context.Provider>;
}

export function useAuthClient(): AuthClient {
  const client = useContext(Context);
  if (!client)
    throw new Error("useAuthClient must be used inside AuthProvider");
  return client;
}

export function useAuth(): AuthState {
  const client = useAuthClient();
  return useSyncExternalStore(
    (listener) => client.subscribe(listener),
    () => client.state,
  );
}

/** Shows protected screens only to a signed-in user; otherwise the sign-in form. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const state = useAuth();
  if (state.status === "loading") return <div aria-busy="true" />;
  if (state.status === "signed-out") return <SignInScreen />;
  return children;
}
