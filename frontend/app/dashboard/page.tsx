"use client";

import { useEffect, useState } from "react";

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

  useEffect(() => {
    async function fetchMe() {
      try {
        const token = localStorage.getItem("access_token");

        if (!token) {
          throw new Error("No token found");
        }

        const response = await fetch(`${API_BASE_URL}/api/auth/me`, {
          headers: {
            Authorization: `Bearer ${token}`,
          },
        });

        const data = await response.json();

        if (!response.ok) {
          throw new Error(data.detail || "Failed to fetch user");
        }

        setUser(data);
      } catch (err) {
        if (err instanceof Error) {
            setError(err.message);
          } else {
            setError("Something went wrong");
          }
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
    <main style={{ padding: "2rem" }}>
      <h1>Dashboard</h1>
      <p><strong>Email:</strong> {user.email}</p>
      <p><strong>User ID:</strong> {user.id}</p>
      <p><strong>Created At:</strong> {user.created_at}</p>
    </main>
  );
}