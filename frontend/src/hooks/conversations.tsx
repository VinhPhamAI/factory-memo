import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useAuthClient } from "@/hooks/auth";
import {
  ConversationError,
  ConversationsApi,
  type Conversation,
} from "@/services/conversations-api";

export const MAX_THINKING_CHARACTERS = 64_000;
const MAX_THINKING_REPLIES = 20;

type Thinking = { reasoning: string; thinkingSeconds?: number };

interface Conversations {
  api: ConversationsApi;
  /** The signed-in user's conversations, most recently active first. */
  list: Conversation[];
  loading: boolean;
  error: string;
  /** Add or move a conversation to the top of the list. */
  remember(conversation: Conversation): void;
  rememberThinking(
    conversationId: string,
    replyId: string,
    thinking: Thinking,
  ): void;
  thinkingFor(conversationId: string, replyId: string): Thinking | undefined;
  rename(id: string, title: string): Promise<void>;
  remove(id: string): Promise<void>;
}

const Context = createContext<Conversations | null>(null);

/** Loads the conversation list once and shares it between sidebar and chat. */
export function ConversationsProvider({ children }: { children: ReactNode }) {
  const auth = useAuthClient();
  const api = useMemo(() => new ConversationsApi(auth), [auth]);
  const [list, setList] = useState<Conversation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const thinkingReplies = useRef(new Map<string, Thinking>());

  useEffect(() => {
    const abort = new AbortController();
    api
      .list(abort.signal)
      .then((items) => {
        setList(items);
        setError("");
      })
      .catch((cause: unknown) => {
        if (!abort.signal.aborted)
          setError(
            cause instanceof ConversationError
              ? cause.message
              : "Không tải được danh sách trò chuyện.",
          );
      })
      .finally(() => {
        if (!abort.signal.aborted) setLoading(false);
      });
    return () => abort.abort();
  }, [api]);

  const remember = useCallback((conversation: Conversation) => {
    setList((items) => [
      conversation,
      ...items.filter((item) => item.id !== conversation.id),
    ]);
  }, []);

  const rememberThinking = useCallback(
    (conversationId: string, replyId: string, thinking: Thinking) => {
      const entries = thinkingReplies.current;
      const key = `${conversationId}/${replyId}`;
      entries.delete(key);
      entries.set(key, thinking);
      const oldest = entries.keys().next();
      if (entries.size > MAX_THINKING_REPLIES && !oldest.done)
        entries.delete(oldest.value);
    },
    [],
  );

  const thinkingFor = useCallback(
    (conversationId: string, replyId: string) =>
      thinkingReplies.current.get(`${conversationId}/${replyId}`),
    [],
  );

  const value: Conversations = {
    api,
    list,
    loading,
    error,
    remember,
    rememberThinking,
    thinkingFor,
    rename: async (id, title) => {
      const renamed = await api.rename(id, title);
      setList((items) =>
        items.map((item) => (item.id === id ? renamed : item)),
      );
    },
    remove: async (id) => {
      await api.remove(id);
      const entries = thinkingReplies.current;
      for (const key of entries.keys())
        if (key.startsWith(`${id}/`)) entries.delete(key);
      setList((items) => items.filter((item) => item.id !== id));
    },
  };
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useConversations(): Conversations {
  const context = useContext(Context);
  if (!context)
    throw new Error(
      "useConversations must be used inside ConversationsProvider",
    );
  return context;
}
