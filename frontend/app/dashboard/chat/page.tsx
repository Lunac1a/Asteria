"use client";

import { Children, isValidElement, useEffect, useState } from "react";
import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import "./chat.css";

type Message = {
  role: "user" | "assistant";
  content: string;
};

type Chat = {
  id: number;
  backendSessionId?: string;
  title: string;
  messages: Message[];
};

type ChatSessionResponse = {
  id: string;
  title: string;
};

type ChatMessageResponse = {
  role: Message["role"];
  content: string;
};

function createDraftChat(): Chat {
  return {
    id: Date.now() + Math.random(),
    title: "New Chat",
    messages: [],
  };
}

function normalizeMarkdown(content: string) {
  return content
    .replace(/\r\n/g, "\n")
    .split(/(```[\s\S]*?```)/g)
    .map((part, index) =>
      index % 2 === 1 ? part.trim() : part.replace(/\n{3,}/g, "\n\n").trim()
    )
    .join("\n\n")
    .trim();
}

function getCodeBlockLanguage(children: ReactNode) {
  const child = Children.toArray(children)[0];

  if (!isValidElement<{ className?: string }>(child)) {
    return null;
  }

  return child.props.className?.match(/language-([\w-]+)/)?.[1] ?? null;
}

function getCodeText(children: ReactNode) {
  return Children.toArray(children).join("").replace(/\n$/, "");
}

const highlightKeywords =
  "abstract|async|await|boolean|break|case|catch|class|const|continue|def|default|do|else|enum|export|extends|false|finally|for|from|function|if|import|in|interface|let|null|return|static|switch|this|throw|true|try|type|undefined|var|while|with|yield";

function highlightCode(code: string) {
  const pattern = new RegExp(
    [
      String.raw`(\/\*[\s\S]*?\*\/|\/\/[^\n]*|#[^\n]*)`,
      String.raw`("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|` + "`" + String.raw`(?:\\.|[^` + "`" + String.raw`\\])*` + "`" + String.raw`)`,
      String.raw`(\b\d+(?:\.\d+)?\b)`,
      String.raw`(\b(?:` + highlightKeywords + String.raw`)\b)`,
      String.raw`(\b[A-Za-z_$][\w$]*(?=\s*\())`,
    ].join("|"),
    "g"
  );

  const parts: ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(code))) {
    if (match.index > lastIndex) {
      parts.push(code.slice(lastIndex, match.index));
    }

    const comment = match[1];
    const string = match[2];
    const number = match[3];
    const keyword = match[4];
    const className = comment
      ? "token-comment"
      : string
      ? "token-string"
      : number
      ? "token-number"
      : keyword
      ? "token-keyword"
      : "token-function";

    parts.push(
      <span className={className} key={`${match.index}-${match[0]}`}>
        {match[0]}
      </span>
    );
    lastIndex = pattern.lastIndex;
  }

  if (lastIndex < code.length) {
    parts.push(code.slice(lastIndex));
  }

  return parts;
}

export default function ChatPage() {
  const [chats, setChats] = useState<Chat[]>([]);

  const [activeChatId, setActiveChatId] = useState<number | null>(null);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);

  const activeChat = chats.find((c) => c.id === activeChatId);
  const activeBackendSessionId = activeChat?.backendSessionId;

  useEffect(() => {
    async function loadSessions() {
      try {
        const token = localStorage.getItem("access_token");

        if (!token) return;

        const response = await fetch(
          `${process.env.NEXT_PUBLIC_API_BASE_URL}/api/chat/sessions`,
          {
            headers: {
              Authorization: `Bearer ${token}`,
            },
          }
        );

        const data: ChatSessionResponse[] = await response.json();

        if (!response.ok) {
          throw new Error("Failed to load sessions");
        }

        const loadedChats: Chat[] = data.map((session) => ({
          id: Date.now() + Math.random(),
          backendSessionId: session.id,
          title: session.title,
          messages: [],
        }));

        setChats(loadedChats);

        if (loadedChats.length > 0) {
          setActiveChatId(loadedChats[0].id);
        } else {
          const draftChat = createDraftChat();
          setChats([draftChat]);
          setActiveChatId(draftChat.id);
        }
      } catch (error) {
        console.error(error);
      }
    }

    loadSessions();
  }, []);

  useEffect(() => {
    async function loadMessages() {
      if (!activeBackendSessionId) return;

      try {
        const token = localStorage.getItem("access_token");

        if (!token) return;

        const response = await fetch(
          `${process.env.NEXT_PUBLIC_API_BASE_URL}/api/chat/sessions/${activeBackendSessionId}/messages`,
          {
            headers: {
              Authorization: `Bearer ${token}`,
            },
          }
        );

        const data: ChatMessageResponse[] = await response.json();

        if (!response.ok) {
          throw new Error("Failed to load messages");
        }

        const loadedMessages: Message[] = data.map((msg) => ({
          role: msg.role,
          content: msg.content,
        }));

        setChats((prevChats) =>
          prevChats.map((chat) =>
            chat.id === activeChatId
              ? {
                  ...chat,
                  messages: loadedMessages,
                }
              : chat
          )
        );
      } catch (error) {
        console.error(error);
      }
    }

    loadMessages();
  }, [activeBackendSessionId, activeChatId]);

  function handleNewChat() {
    const newChat = createDraftChat();

    setChats((prev) => [newChat, ...prev]);
    setActiveChatId(newChat.id);
  }

  async function handleSend() {
    if (!input.trim() || loading || !activeChat) return;

    const trimmedInput = input.trim();

    const userMessage: Message = {
      role: "user",
      content: trimmedInput,
    };

    const currentBackendSessionId = activeChat.backendSessionId;

    setChats((prevChats) =>
      prevChats.map((chat) =>
        chat.id === activeChatId
          ? {
              ...chat,
              title:
                chat.messages.length === 0
                  ? trimmedInput.slice(0, 24)
                  : chat.title,
              messages: [...chat.messages, userMessage],
            }
          : chat
      )
    );

    setInput("");
    setLoading(true);

    try {
      const token = localStorage.getItem("access_token");

      if (!token) {
        throw new Error("Not authenticated");
      }

      const response = await fetch(
        `${process.env.NEXT_PUBLIC_API_BASE_URL}/api/chat`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({
            message: trimmedInput,
            session_id: currentBackendSessionId ?? null,
          }),
        }
      );

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data?.detail || "Chat request failed");
      }

      const assistantMessage: Message = {
        role: "assistant",
        content: data.answer,
      };

      setChats((prevChats) =>
        prevChats.map((chat) =>
          chat.id === activeChatId
            ? {
                ...chat,
                backendSessionId: data.session_id,
                messages: [...chat.messages, assistantMessage],
              }
            : chat
        )
      );
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Something went wrong";

      setChats((prevChats) =>
        prevChats.map((chat) =>
          chat.id === activeChatId
            ? {
                ...chat,
                messages: [
                  ...chat.messages,
                  {
                    role: "assistant",
                    content: `Error: ${message}`,
                  },
                ],
              }
            : chat
        )
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="chat-app">
      <aside className="chat-sidebar">
        <button className="btn btn-primary" onClick={handleNewChat}>
          + New Chat
        </button>

        <div className="chat-list">
          {chats.map((chat) => (
            <div
              key={chat.id}
              className={
                chat.id === activeChatId
                  ? "chat-list-item active"
                  : "chat-list-item"
              }
              onClick={() => setActiveChatId(chat.id)}
            >
              {chat.title}
            </div>
          ))}
        </div>
      </aside>

      <div className="chat-main">
        <div className="chat-messages-panel">
          {!activeChat || activeChat.messages.length === 0 ? (
            <div className="chat-empty-state">
              <h2>Welcome to Asteria</h2>
              <p>
                Start a learning session to explore an idea or work through a
                question.
              </p>
            </div>
          ) : (
            <div className="chat-message-list">
              {activeChat.messages.map((msg, index) => (
                <div
                  key={index}
                  className={
                    msg.role === "user"
                      ? "chat-message user"
                      : "chat-message assistant"
                  }
                >
                  <div className="markdown-content">
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm]}
                      components={{
                        a: ({ children, ...props }) => (
                          <a {...props} target="_blank" rel="noreferrer">
                            {children}
                          </a>
                        ),
                        pre: ({ children }) => {
                          const language = getCodeBlockLanguage(children);

                          return (
                            <div className="markdown-code-block">
                              {language ? (
                                <div className="markdown-code-language">
                                  {language}
                                </div>
                              ) : null}
                              <pre>{children}</pre>
                            </div>
                          );
                        },
                        code: ({ className, children, ...props }) => {
                          const language = className?.match(
                            /language-([\w-]+)/
                          )?.[1];

                          if (!language) {
                            return (
                              <code className={className} {...props}>
                                {children}
                              </code>
                            );
                          }

                          return (
                            <code className={className} {...props}>
                              {highlightCode(getCodeText(children))}
                            </code>
                          );
                        },
                      }}
                    >
                      {normalizeMarkdown(msg.content)}
                    </ReactMarkdown>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="chat-composer">
          <input
            className="chat-composer-input"
            placeholder="Ask to help you think through something..."
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !loading) {
                handleSend();
              }
            }}
            disabled={loading}
          />

          <button
            className="btn btn-primary"
            onClick={handleSend}
            disabled={loading}
          >
            {loading ? "Thinking..." : "Send"}
          </button>
        </div>
      </div>
    </div>
  );
}
