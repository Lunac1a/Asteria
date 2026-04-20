"use client";

import { useState } from "react";

type Message = {
  role: "user" | "assistant";
  content: string;
};

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");

  function handleSend() {
    if (!input.trim()) return;

    const newMessages: Message[] = [
      ...messages,
      { role: "user", content: input },
      {
        role: "assistant",
        content: "This is a placeholder response from the AI.",
      },
    ];

    setMessages(newMessages);
    setInput("");
  }

  return (
    <div className="chat-page">
      <div className="chat-workspace">
        <div className="chat-header">
          <h1 className="chat-title">Chat</h1>
          <p className="chat-subtitle">
            Interact with your personal knowledge through grounded AI assistance.
          </p>
        </div>

        <div className="chat-messages-panel">
          {messages.length === 0 ? (
            <div className="chat-empty-state">
              <h2>Welcome to your Copilot</h2>
              <p>Ask a question to start your first conversation.</p>
            </div>
          ) : (
            <div className="chat-message-list">
              {messages.map((msg, index) => (
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
            type="text"
            placeholder="Ask something about your knowledge..."
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                handleSend();
              }
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