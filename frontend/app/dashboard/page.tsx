"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

const API_BASE_URL = "";

type User = {
  id: string;
  email: string;
  created_at: string;
};

export default function DashboardPage() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();

  useEffect(() => {
    function redirectToLogin() {
      localStorage.removeItem("access_token");
      router.replace("/auth/login");
    }

    async function fetchMe() {
      try {
        const token = localStorage.getItem("access_token");

        if (!token) {
          redirectToLogin();
          return;
        }

        const response = await fetch(`${API_BASE_URL}/api/me`, {
          headers: {
            Authorization: `Bearer ${token}`,
          },
        });

        const data = await response.json();

        if (!response.ok) {
          redirectToLogin();
          return;
        }

        setUser(data);
      } catch {
        redirectToLogin();
      } finally {
        setLoading(false);
      }
    }

    fetchMe();
  }, [router]);

  if (loading) return <p>Loading...</p>;
  if (!user) return <p>No user</p>;

  return (
    <div className="section">
      <h1 className="section-title">Welcome to Asteria</h1>
      <p className="card-text">
        Your AI learning copilot is ready to help you explore ideas, revisit
        concepts, and turn knowledge into understanding.
      </p>
      <a href="/dashboard/chat" className="btn btn-primary">Open your learning workspace</a>
    </div>
  );
}
