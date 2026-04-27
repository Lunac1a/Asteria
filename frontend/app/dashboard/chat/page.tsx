"use client";

import { useState } from "react";
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

export default function ChatPage() {
  const [chats, setChats] = useState<Chat[]>([
    {
      id: 1,
      title: "First Chat",
      messages: [],
    },
  ]);

  const [activeChatId, setActiveChatId] = useState(1);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);

  const activeChat = chats.find((c) => c.id === activeChatId);

  function handleNewChat() {
    const newChat: Chat = {
      id: Date.now(),
      title: "New Chat",
      messages: [],
    };

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

  if (!activeChat) {
    return <p>No active chat</p>;
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
          {activeChat.messages.length === 0 ? (
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
