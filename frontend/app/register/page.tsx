"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL;

export default function RegisterPage() {
  const router = useRouter();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");

  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [loading, setLoading] = useState(false);

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
      const response = await fetch(`${API_BASE_URL}/api/auth/register`, {
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
            errorMessage = data.detail.map((item: { msg: any; }) => item.msg).join(", ");
        }

      throw new Error(errorMessage);
      }

      setSuccess("Registration successful. Redirecting to login...");
      setTimeout(() => {
        router.push("/login");
      }, 1000);
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
    <main style={styles.page}>
      <div style={styles.card}>
        <h1 style={styles.title}>Create your account</h1>
        <p style={styles.subtitle}>
          Start building your personal knowledge workspace.
        </p>

        <form onSubmit={handleRegister} style={styles.form}>
          <input
            type="email"
            placeholder="Email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            style={styles.input}
          />

          <input
            type="password"
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            style={styles.input}
          />

          <input
            type="password"
            placeholder="Confirm password"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            required
            style={styles.input}
          />

          <button type="submit" disabled={loading} style={styles.button}>
            {loading ? "Creating account..." : "Register"}
          </button>
        </form>

        {error && <p style={styles.error}>{error}</p>}
        {success && <p style={styles.success}>{success}</p>}

        <p style={styles.footerText}>
          Already have an account?{" "}
          <Link href="/login" style={styles.link}>
            Login
          </Link>
        </p>
      </div>
    </main>
  );
}

const styles: Record<string, React.CSSProperties> = {
  page: {
    minHeight: "100vh",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#f7f9fc",
    padding: "24px",
    fontFamily: "Arial, sans-serif",
  },
  card: {
    width: "100%",
    maxWidth: "420px",
    backgroundColor: "#ffffff",
    borderRadius: "18px",
    padding: "32px",
    boxShadow: "0 8px 24px rgba(15, 23, 42, 0.08)",
  },
  title: {
    margin: "0 0 8px",
    fontSize: "28px",
    fontWeight: 700,
    color: "#111827",
  },
  subtitle: {
    margin: "0 0 24px",
    color: "#4b5563",
    lineHeight: 1.6,
  },
  form: {
    display: "grid",
    gap: "14px",
  },
  input: {
    padding: "14px 16px",
    borderRadius: "12px",
    border: "1px solid #d1d5db",
    fontSize: "15px",
    outline: "none",
  },
  button: {
    marginTop: "8px",
    padding: "14px 16px",
    borderRadius: "12px",
    border: "none",
    backgroundColor: "#2563eb",
    color: "#ffffff",
    fontSize: "15px",
    fontWeight: 600,
    cursor: "pointer",
  },
  error: {
    marginTop: "16px",
    color: "#dc2626",
  },
  success: {
    marginTop: "16px",
    color: "#16a34a",
  },
  footerText: {
    marginTop: "20px",
    color: "#4b5563",
    fontSize: "14px",
  },
  link: {
    color: "#2563eb",
    textDecoration: "none",
    fontWeight: 600,
  },
};