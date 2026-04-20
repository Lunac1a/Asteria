import Link from "next/link";

export default function LandingPage() {
  return (
    <main className="page-shell">
      <div className="container">
        <nav className="navbar">
          <div className="nav-brand">Personal Knowledge Copilot</div>

          <div className="nav-actions">
            <Link href="/login">Login</Link>
            <Link href="/register" className="btn btn-primary">
              Register
            </Link>
          </div>
        </nav>

        <section className="hero">
          <div className="hero-content">
            <p className="badge">AI-powered learning assistant</p>

            <h1 className="hero-title">
              Learn with your own knowledge, not generic answers.
            </h1>

            <p className="hero-subtitle">
              Personal Knowledge Copilot helps you organize, retrieve, and
              interact with your own documents through grounded AI assistance.
            </p>

            <div className="hero-actions">
              <Link href="/register" className="btn btn-primary">
                Try it out
              </Link>
            </div>
          </div>
        </section>

        <section className="section">
          <h2 className="section-title">Core features</h2>

          <div className="grid-3">
            <div className="feature-card">
              <h3 className="card-title">Upload your knowledge</h3>
              <p className="card-text">
                Build your own knowledge base from personal notes, documents,
                and study materials.
              </p>
            </div>

            <div className="feature-card">
              <h3 className="card-title">Chat with grounded context</h3>
              <p className="card-text">
                Get responses based on your uploaded content instead of generic,
                ungrounded answers.
              </p>
            </div>

            <div className="feature-card">
              <h3 className="card-title">Learn through guided thinking</h3>
              <p className="card-text">
                Use AI as a learning companion that supports understanding,
                reflection, and structured thinking.
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
              <h3 className="card-title">Copilot Chat</h3>
              <div className="chat-bubble-user">
                Summarize the key ideas from my uploaded notes.
              </div>
              <div className="chat-bubble-bot">
                I found three main themes in your notes: retrieval grounding,
                structured learning, and production-ready system design.
              </div>
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}