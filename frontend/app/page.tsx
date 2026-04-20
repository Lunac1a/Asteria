import Link from "next/link";

export default function LandingPage() {
  return (
    <main style={styles.page}>
      <nav style={styles.nav}>
        <div style={styles.logo}>Personal Knowledge Copilot</div>

        <div style={styles.navActions}>
          <Link href="/login" style={styles.navLink}>
            Login
          </Link>
          <Link href="/register" style={styles.primaryNavButton}>
            Register
          </Link>
        </div>
      </nav>

      <section style={styles.hero}>
        <div style={styles.heroContent}>
          <p style={styles.badge}>AI-powered learning assistant</p>

          <h1 style={styles.heroTitle}>
            Learn with your own knowledge, not generic answers.
          </h1>

          <p style={styles.heroSubtitle}>
            Personal Knowledge Copilot helps you organize, retrieve, and
            interact with your own documents through grounded AI assistance.
          </p>

          <div style={styles.heroActions}>
            <Link href="/register" style={styles.primaryButton}>
              Try it out
            </Link>
          </div>
        </div>
      </section>

      <section style={styles.featuresSection}>
        <h2 style={styles.sectionTitle}>Core features</h2>

        <div style={styles.cardGrid}>
          <div style={styles.card}>
            <h3 style={styles.cardTitle}>Upload your knowledge</h3>
            <p style={styles.cardText}>
              Build your own knowledge base from personal notes, documents, and
              study materials.
            </p>
          </div>

          <div style={styles.card}>
            <h3 style={styles.cardTitle}>Chat with grounded context</h3>
            <p style={styles.cardText}>
              Get responses based on your uploaded content instead of generic,
              ungrounded answers.
            </p>
          </div>

          <div style={styles.card}>
            <h3 style={styles.cardTitle}>Learn through guided thinking</h3>
            <p style={styles.cardText}>
              Use AI as a learning companion that supports understanding,
              reflection, and structured thinking.
            </p>
          </div>
        </div>
      </section>

      <section style={styles.previewSection}>
        <h2 style={styles.sectionTitle}>Product preview</h2>

        <div style={styles.previewGrid}>
          <div style={styles.previewCard}>
            <h3 style={styles.cardTitle}>Knowledge Base</h3>
            <ul style={styles.previewList}>
              <li>Week 5 lecture notes.pdf</li>
              <li>Database revision.md</li>
              <li>AI project brief.docx</li>
              <li>Reading summary.txt</li>
            </ul>
          </div>

          <div style={styles.previewCard}>
            <h3 style={styles.cardTitle}>Copilot Chat</h3>
            <div style={styles.chatBubbleUser}>
              Summarize the key ideas from my uploaded notes.
            </div>
            <div style={styles.chatBubbleBot}>
              I found three main themes in your notes: retrieval grounding,
              structured learning, and production-ready system design.
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}

const styles: Record<string, React.CSSProperties> = {
  page: {
    minHeight: "100vh",
    backgroundColor: "#f7f9fc",
    color: "#111827",
    padding: "0 24px 48px",
    fontFamily: "Arial, sans-serif",
  },
  nav: {
    maxWidth: "1100px",
    margin: "0 auto",
    height: "72px",
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
  },
  logo: {
    fontSize: "20px",
    fontWeight: 700,
  },
  navActions: {
    display: "flex",
    gap: "12px",
    alignItems: "center",
  },
  navLink: {
    textDecoration: "none",
    color: "#111827",
    fontWeight: 500,
    padding: "10px 14px",
  },
  primaryNavButton: {
    textDecoration: "none",
    backgroundColor: "#2563eb",
    color: "#ffffff",
    padding: "10px 16px",
    borderRadius: "10px",
    fontWeight: 600,
  },
  hero: {
    maxWidth: "1100px",
    margin: "0 auto",
    padding: "64px 0 56px",
  },
  heroContent: {
    maxWidth: "720px",
  },
  badge: {
    display: "inline-block",
    backgroundColor: "#dbeafe",
    color: "#1d4ed8",
    padding: "8px 12px",
    borderRadius: "999px",
    fontSize: "14px",
    fontWeight: 600,
    marginBottom: "20px",
  },
  heroTitle: {
    fontSize: "52px",
    lineHeight: 1.1,
    fontWeight: 800,
    margin: "0 0 20px",
  },
  heroSubtitle: {
    fontSize: "18px",
    lineHeight: 1.7,
    color: "#4b5563",
    margin: "0 0 28px",
  },
  heroActions: {
    display: "flex",
    gap: "14px",
    flexWrap: "wrap",
  },
  primaryButton: {
    textDecoration: "none",
    backgroundColor: "#2563eb",
    color: "#ffffff",
    padding: "14px 20px",
    borderRadius: "12px",
    fontWeight: 600,
  },
  secondaryButton: {
    textDecoration: "none",
    backgroundColor: "#ffffff",
    color: "#111827",
    padding: "14px 20px",
    borderRadius: "12px",
    fontWeight: 600,
    border: "1px solid #d1d5db",
  },
  featuresSection: {
    maxWidth: "1100px",
    margin: "0 auto",
    padding: "32px 0",
  },
  sectionTitle: {
    fontSize: "28px",
    fontWeight: 700,
    marginBottom: "20px",
  },
  cardGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
    gap: "20px",
  },
  card: {
    backgroundColor: "#ffffff",
    borderRadius: "18px",
    padding: "24px",
    boxShadow: "0 8px 24px rgba(15, 23, 42, 0.06)",
  },
  cardTitle: {
    fontSize: "18px",
    fontWeight: 700,
    margin: "0 0 12px",
  },
  cardText: {
    color: "#4b5563",
    lineHeight: 1.7,
    margin: 0,
  },
  previewSection: {
    maxWidth: "1100px",
    margin: "0 auto",
    padding: "32px 0 0",
  },
  previewGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
    gap: "20px",
  },
  previewCard: {
    backgroundColor: "#ffffff",
    borderRadius: "18px",
    padding: "24px",
    boxShadow: "0 8px 24px rgba(15, 23, 42, 0.06)",
    minHeight: "220px",
  },
  previewList: {
    paddingLeft: "18px",
    color: "#4b5563",
    lineHeight: 1.9,
    margin: 0,
  },
  chatBubbleUser: {
    backgroundColor: "#eff6ff",
    color: "#1e3a8a",
    padding: "12px 14px",
    borderRadius: "12px",
    marginBottom: "12px",
    lineHeight: 1.6,
  },
  chatBubbleBot: {
    backgroundColor: "#f3f4f6",
    color: "#374151",
    padding: "12px 14px",
    borderRadius: "12px",
    lineHeight: 1.6,
  },
};