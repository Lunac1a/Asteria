"use client";
import { useI18n, t } from "../lib/i18n";
import Image from "next/image";
import Link from "next/link";
import "./page.css";

export default function LandingPage() {
 useI18n();
  return (
    <main className="page-shell">
      <div className="container">
        <nav className="navbar">
          <div className="nav-brand">
            <span className="brand-mark" aria-hidden="true" />
            <span>Asteria</span>
          </div>

          <div className="nav-actions">
            <Link href="/auth/login">{t("Login")}</Link>
            <Link href="/auth/register" className="btn btn-primary">
              {t("Register")}</Link>
          </div>
        </nav>

        <section className="hero">
          <div className="hero-content">
            <p className="badge">{t("AI Learning Copilot")}</p>

            <h1 className="hero-title">{t("Think, not just answer.")}</h1>

            <p className="hero-subtitle">
              {t("Asteria is an AI learning copilot that helps you explore ideas, build understanding, and learn through guided interaction with your knowledge.")}</p>

            <div className="hero-actions">
              <Link href="/auth/register" className="btn btn-primary">
                {t("Start learning with Asteria")}</Link>
            </div>
          </div>

          <div className="hero-visual" aria-hidden="true">
            <Image
              className="hero-illustration"
              src="/asteria.svg"
              alt=""
              width={540}
              height={540}
              priority
            />
          </div>
        </section>

        <section className="section">
          <h2 className="section-title">{t("Core features")}</h2>

          <div className="grid-3">
            <div className="feature-card">
              <h3 className="card-title">{t("Guided learning")}</h3>
              <p className="card-text">
                {t("Turn notes, readings, and study materials into a learning flow that helps you build real understanding.")}</p>
            </div>

            <div className="feature-card">
              <h3 className="card-title">{t("Knowledge exploration")}</h3>
              <p className="card-text">
                {t("Explore concepts through grounded conversations that stay tied to your material instead of drifting into generic replies.")}</p>
            </div>

            <div className="feature-card">
              <h3 className="card-title">{t("Structured conversation")}</h3>
              <p className="card-text">
                {t("Work through questions step by step with an assistant designed to support thinking, reflection, and deeper learning.")}</p>
            </div>
          </div>
        </section>

        <section className="section">
          <h2 className="section-title">{t("Product preview")}</h2>

          <div className="grid-2">
            <div className="preview-card">
              <h3 className="card-title">{t("Knowledge Base")}</h3>
              <ul className="list">
                <li>Week 5 lecture notes.pdf</li>
                <li>Database revision.md</li>
                <li>AI project brief.docx</li>
                <li>Reading summary.txt</li>
              </ul>
            </div>

            <div className="preview-card">
              <h3 className="card-title">{t("Learning Session")}</h3>
              <div className="chat-bubble-user">
                Help me connect the key ideas from my uploaded notes.
              </div>
              <div className="chat-bubble-bot">
                Your notes point to three themes worth unpacking: grounded
                retrieval, guided learning, and systems thinking. Want to work
                through them one by one?
              </div>
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}
