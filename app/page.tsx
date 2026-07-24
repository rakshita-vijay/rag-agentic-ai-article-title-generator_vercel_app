"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import ReactMarkdown from "react-markdown";
import { createClient } from "@/lib/supabase/client";

type HistoryItem = {
  id: string;
  theme: string;
  content: string;
  topic_count: number;
  created_at: string;
};

export default function Home() {
  const router = useRouter();
  const supabase = createClient();

  const [theme, setTheme] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{
    content: string;
    theme: string;
    topic_count: number;
  } | null>(null);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [userEmail, setUserEmail] = useState<string | null>(null);

  useEffect(() => {
    loadUser();
    loadHistory();
  }, []);

  async function loadUser() {
    const {
      data: { user },
    } = await supabase.auth.getUser();
    setUserEmail(user?.email ?? null);
  }

  async function loadHistory() {
    const { data } = await supabase
      .from("history")
      .select("*")
      .order("created_at", { ascending: false })
      .limit(5);
    if (data) setHistory(data as HistoryItem[]);
  }

  async function handleSignOut() {
    await supabase.auth.signOut();
    router.push("/login");
    router.refresh();
  }

  async function handleGenerate(e: React.FormEvent) {
    e.preventDefault();
    if (!theme) return;

    setLoading(true);
    setError(null);
    setResult(null);

    const numTopics = Math.floor(Math.random() * 6) + 5; // 5-10, same as original

    try {
      const {
        data: { session },
      } = await supabase.auth.getSession();

      const res = await fetch("/api/generate", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${session?.access_token ?? ""}`,
        },
        body: JSON.stringify({ theme, num_topics: numTopics }),
      });

      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.error || "Generation failed");
      }

      setResult({
        content: data.content,
        theme,
        topic_count: numTopics,
      });

      // Save to history (RLS ensures this only ever writes the caller's own row)
      await supabase.from("history").insert({
        theme,
        content: data.content,
        topic_count: numTopics,
      });

      await loadHistory();
    } catch (err: any) {
      setError(err.message || "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  function downloadMarkdown(content: string, forTheme: string) {
    const ts = new Date();
    const stamp = `${ts.getDate()}_${ts.getMonth() + 1}_${ts.getFullYear()}_${ts.getHours()}_${ts.getMinutes()}_${ts.getSeconds()}`;
    const filename = `article_topics_${forTheme.replace(/\s+/g, "_")}_${stamp}.md`;

    const blob = new Blob([content], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function deleteHistoryItem(id: string) {
    await supabase.from("history").delete().eq("id", id);
    setHistory((h) => h.filter((item) => item.id !== id));
  }

  return (
    <div className="layout">
      <div className="main-col">
        <div className="topbar">
          <div>
            <h1>✨ Article Topic Generator</h1>
            <h2 style={{ fontSize: 14 }}>Create engaging article topics with AI</h2>
          </div>
          <div>
            {userEmail && <p style={{ marginBottom: 6 }}>{userEmail}</p>}
            <button className="signout-btn" onClick={handleSignOut}>
              Sign out
            </button>
          </div>
        </div>

        <form onSubmit={handleGenerate} style={{ margin: "20px 0" }}>
          <label>Enter theme:</label>
          <input
            type="text"
            placeholder="e.g., Artificial Intelligence"
            value={theme}
            onChange={(e) => setTheme(e.target.value)}
          />
          <div style={{ marginTop: 12 }}>
            <button type="submit" disabled={loading || !theme}>
              {loading ? "🔮 AI agents are working..." : "🚀 Generate Topics"}
            </button>
          </div>
        </form>

        {error && <div className="notification error">❌ {error}</div>}

        {result && (
          <>
            <h3>📝 Generated Topics</h3>
            <div className="content-box">
              <ReactMarkdown>{result.content}</ReactMarkdown>
            </div>
            <a
              className="download-link"
              onClick={() => downloadMarkdown(result.content, result.theme)}
            >
              📥 Download Topics
            </a>
          </>
        )}

        <p style={{ marginTop: 40, opacity: 0.7 }}>
          🤖 Powered by CrewAI &amp; Gemini 2.0 Flash
        </p>
      </div>

      <div className="sidebar">
        <h2 style={{ fontSize: 16 }}>📚 Generation History</h2>
        {history.length === 0 && <p>No history yet</p>}
        {history.map((item) => {
          const isExpanded = !!expanded[item.id];
          return (
            <div className="history-item" key={item.id}>
              <strong>
                {item.theme} ({item.topic_count} topics)
              </strong>
              <p style={{ fontSize: 12, opacity: 0.8 }}>
                {new Date(item.created_at).toLocaleString()}
              </p>

              {isExpanded ? (
                <ReactMarkdown>{item.content}</ReactMarkdown>
              ) : (
                <p>{item.content.slice(0, 200)}...</p>
              )}

              <div className="history-actions">
                <button
                  onClick={() =>
                    setExpanded((e) => ({ ...e, [item.id]: !isExpanded }))
                  }
                >
                  {isExpanded ? "🔙 Collapse" : "🔎 Expand"}
                </button>
                <button
                  onClick={() => downloadMarkdown(item.content, item.theme)}
                >
                  📥 Download
                </button>
                <button
                  className="delete-btn"
                  onClick={() => deleteHistoryItem(item.id)}
                >
                  🗑️ Delete
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
