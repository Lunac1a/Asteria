"use client";

import { useState } from "react";

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
  const [chats, setChats] = useState<Chat[]>([
    {
      id: 1,
      title: "First Chat",
      messages: [],
    },
  ]);

  const [activeChatId, setActiveChatId] = useState(1);
  const [input, setInput] = useState("");

  const activeChat = chats.find((c) => c.id === activeChatId)!;

  function handleNewChat() {
    const newChat: Chat = {
      id: Date.now(),
      title: "New Chat",
      messages: [],
    };

    setChats([newChat, ...chats]);
    setActiveChatId(newChat.id);
  }

  function handleSend() {
    if (!input.trim()) return;

    const newMessages: Message[] = [
      { role: "user", content: input },
      {
        role: "assistant",
        content: "This is a placeholder response from the AI.",
      },
    ];

    const updatedChats: Chat[] = chats.map((chat) => {
      if (chat.id !== activeChatId) return chat;

      return {
        ...chat,
        messages: [...chat.messages, ...newMessages],
      };
    });

    setChats(updatedChats);
    setInput("");
  }

  return (
    <div className="chat-app">
      {/* Sidebar */}
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

      {/* Main */}
      <div className="chat-main">
        <div className="chat-messages-panel">
          {activeChat.messages.length === 0 ? (
            <div className="chat-empty-state">
              <h2>Welcome to your Copilot</h2>
              <p>Ask a question to start your conversation.</p>
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
                  {msg.content}
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="chat-composer">
          <input
            className="chat-composer-input"
            placeholder="Ask something..."
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleSend();
            }}
          />

          <button className="btn btn-primary" onClick={handleSend}>
            Send
          </button>
        </div>
      </div>
    </div>
  );
}