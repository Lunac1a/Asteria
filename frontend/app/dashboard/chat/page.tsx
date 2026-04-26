"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useRef, useEffect } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import "./chat.css";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL;
const messagesEndRef = useRef<HTMLDivElement | null>(null);

type Message = {
  role: "user" | "assistant";
  content: string;
};

type Chat = {
  id: number;
  title: string;
  messages: Message[];
};

export default function ChatPage() {
  const router = useRouter();

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

  const activeChat = chats.find((c) => c.id === activeChatId)!;

  function scrollToBottom() {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }

  useEffect(() => {
    scrollToBottom();
  }, [activeChat.messages]);

  function handleNewChat() {
    const newChat: Chat = {
      id: Date.now(),
      title: "New Chat",
      messages: [],
    };

    setChats((prev) => [newChat, ...prev]);
    setActiveChatId(newChat.id);
  }

  function updateActiveChatMessages(newMessages: Message[]) {
    setChats((prevChats) =>
      prevChats.map((chat) =>
        chat.id === activeChatId
          ? {
              ...chat,
              messages: [...chat.messages, ...newMessages],
            }
          : chat
      )
    );
  }

  function updateActiveChatTitle(firstMessage: string) {
    setChats((prevChats) =>
      prevChats.map((chat) => {
        if (chat.id !== activeChatId) return chat;
        if (chat.messages.length > 0) return chat;

        return {
          ...chat,
          title: firstMessage.slice(0, 24) || "New Chat",
        };
      })
    );
  }

  async function handleSend() {
    if (!input.trim() || loading) return;

    const trimmedInput = input.trim();
    const userMessage: Message = {
      role: "user",
      content: trimmedInput,
    };

    updateActiveChatTitle(trimmedInput);
    updateActiveChatMessages([userMessage]);
    setInput("");
    setLoading(true);

    try {
      const token = localStorage.getItem("access_token");

      if (!token) {
        localStorage.removeItem("access_token");
        router.replace("/auth/login");
        return;
      }

      const response = await fetch(`${API_BASE_URL}/api/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          message: trimmedInput,
        }),
      });

      const data = await response.json().catch(() => null);

      if (response.status === 401) {
        localStorage.removeItem("access_token");
        router.replace("/auth/login");
        return;
      }

      if (!response.ok) {
        const errorMessage =
          data?.detail || "Something went wrong while talking to the AI.";

        updateActiveChatMessages([
          {
            role: "assistant",
            content:
              errorMessage === "LLM settings not configured"
                ? "⚠️ You need to configure your API key in Settings before using chat."
                : `⚠️ ${errorMessage}`,
          },
        ]);
        return;
      }

      updateActiveChatMessages([
        {
          role: "assistant",
          content: data.answer,
        },
      ]);
    } catch {
      updateActiveChatMessages([
        {
          role: "assistant",
          content: "Error: Failed to connect to the server.",
        },
      ]);
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
                  {msg.role === "assistant" ? (
                    <div className="markdown-content">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {msg.content}
                      </ReactMarkdown>
                    </div>
                  ) : (
                    msg.content
                  )}
                </div>
              ))}
              <div ref={messagesEndRef} />
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
