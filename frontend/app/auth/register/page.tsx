"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import "../auth.css";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL;

export default function RegisterPage() {
  const router = useRouter();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");

  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const token = localStorage.getItem("access_token");

    if (token) {
      router.replace("/dashboard");
    }
  }, [router]);

  async function handleRegister(e: { preventDefault: () => void; }) {
    e.preventDefault();
    setError("");
    setSuccess("");

    if (password !== confirmPassword) {
      setError("Passwords do not match");
      return;
    }

    setLoading(true);

    try {
      const response = await fetch(`${API_BASE_URL}/api/register`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          email,
          password,
        }),
      });

      const data = await response.json();

      if (!response.ok) {
        let errorMessage = "Register failed";

        if (typeof data.detail === "string") {
            errorMessage = data.detail;
        } else if (Array.isArray(data.detail) && data.detail.length > 0) {
            errorMessage = data.detail.map((item: { msg: string; }) => item.msg).join(", ");
        }

      throw new Error(errorMessage);
      }

      setSuccess("Registration successful. Redirecting to login...");
      setTimeout(() => {
        router.push("/login");
      }, 100);
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

  return (
    <main className="auth-page">
      <div className="auth-card">
        <div className="auth-back">
          <Link href="/public" className="back-link">
            ← Back
          </Link>
        </div>
        <h1 className="auth-title">Create your account</h1>
        <p className="auth-subtitle">
          Start learning with Asteria, your AI learning copilot.
        </p>

        <form onSubmit={handleRegister} className="form-stack">
          <input
            type="email"
            placeholder="Email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            className="input"
          />

          <input
            type="password"
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            className="input"
          />

          <input
            type="password"
            placeholder="Confirm password"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            required
            className="input"
          />

          <button type="submit" disabled={loading} className="btn btn-primary">
            {loading ? "Creating account..." : "Register"}
          </button>
        </form>

        {error && <p className="error-text">{error}</p>}
        {success && <p className="success-text">{success}</p>}

        <p className="helper-text">
          Already have an account?{" "}
          <Link href="/login" className="text-link">
            Login
          </Link>
        </p>
      </div>
    </main>
  );
}
