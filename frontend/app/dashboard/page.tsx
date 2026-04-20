"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL;

type User = {
  id: string;
  email: string;
  created_at: string;
};

export default function DashboardPage() {
  const [user, setUser] = useState<User | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const router = useRouter();

  useEffect(() => {
    async function fetchMe() {
      try {
        const token = localStorage.getItem("access_token");

        if (!token) {
          router.push("/login");
          return;
        }

        const response = await fetch(`${API_BASE_URL}/api/auth/me`, {
          headers: {
            Authorization: `Bearer ${token}`,
          },
        });

        const data = await response.json();

        if (!response.ok) {
          router.push("/login");
          return;
        }

        setUser(data);
      } catch (err) {
        router.push("/login")
      } finally {
        setLoading(false);
      }
    }

    fetchMe();
  }, []);

  if (loading) return <p>Loading...</p>;
  if (error) return <p style={{ color: "red" }}>{error}</p>;
  if (!user) return <p>No user</p>;

  return (
    <div className="section">
      <h1 className="section-title">Dashboard</h1>
      <p className="card-text">Dashboard content coming soon.</p>
    </div>
  );
}