import Image from "next/image";
import Link from "next/link";

export default function LandingPage() {
  return (
    <main className="page-shell">
      <div className="container">
        <nav className="navbar">
          <div className="nav-brand">Asteria</div>

          <div className="nav-actions">
            <Link href="/login">Login</Link>
            <Link href="/register" className="btn btn-primary">
              Register
            </Link>
          </div>
        </nav>

        <section className="hero">
          <div className="hero-content">
            <p className="badge">AI Learning Copilot</p>

            <h1 className="hero-title">Think, not just answer.</h1>

            <p className="hero-subtitle">
              Asteria is an AI learning copilot that helps you explore ideas,
              build understanding, and learn through guided interaction with
              your knowledge.
            </p>

            <div className="hero-actions">
              <Link href="/register" className="btn btn-primary">
                Start learning with Asteria
              </Link>
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
          <h2 className="section-title">Core features</h2>

          <div className="grid-3">
            <div className="feature-card">
              <h3 className="card-title">Guided learning</h3>
              <p className="card-text">
                Turn notes, readings, and study materials into a learning flow
                that helps you build real understanding.
              </p>
            </div>

            <div className="feature-card">
              <h3 className="card-title">Knowledge exploration</h3>
              <p className="card-text">
                Explore concepts through grounded conversations that stay tied
                to your material instead of drifting into generic replies.
              </p>
            </div>

            <div className="feature-card">
              <h3 className="card-title">Structured conversation</h3>
              <p className="card-text">
                Work through questions step by step with an assistant designed
                to support thinking, reflection, and deeper learning.
              </p>
            </div>
          </div>
        </section>

        <section className="section">
          <h2 className="section-title">Product preview</h2>

          <div className="grid-2">
            <div className="preview-card">
              <h3 className="card-title">Knowledge Base</h3>
              <ul className="list">
                <li>Week 5 lecture notes.pdf</li>
                <li>Database revision.md</li>
                <li>AI project brief.docx</li>
                <li>Reading summary.txt</li>
              </ul>
            </div>

            <div className="preview-card">
              <h3 className="card-title">Learning Session</h3>
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
