"use client";

export const dynamic = "force-dynamic";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import ReactMarkdown from "react-markdown";
import { createClient } from "../lib/supabase/client";

type HistoryItem = {
  id: string;
  theme: string;
  content: string;
  topic_count: number;
  created_at: string;
};

type StageKey = "plan" | "research" | "condense" | "write";

type StageTiming = { start: number; end?: number };

const STAGE_DEFS: { key: StageKey; icon: string; label: string }[] = [
  { key: "plan", icon: "🔮", label: "Planning topics" },
  { key: "research", icon: "🔎", label: "Researching" },
  { key: "condense", icon: "🧵", label: "Condensing & collecting links" },
  { key: "write", icon: "✍️", label: "Writing final output" },
];

function formatDuration(ms: number) {
  return `${(ms / 1000).toFixed(1)}s`;
}

export default function Home() {
  const router = useRouter();
  const supabase = createClient();

  const [theme, setTheme] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  // The single history item currently expanded in the main panel (only one at a time)
  const [activeId, setActiveId] = useState<string | null>(null);
  const [userEmail, setUserEmail] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Live stage timer state
  const [activeStage, setActiveStage] = useState<StageKey | null>(null);
  const [stageTimes, setStageTimes] = useState<Partial<Record<StageKey, StageTiming>>>({});
  const [genStart, setGenStart] = useState<number | null>(null);
  const [genEnd, setGenEnd] = useState<number | null>(null);
  const [, forceTick] = useState(0);

  useEffect(() => {
    loadUser();
    loadHistory();

    if (typeof Notification !== "undefined" && Notification.permission === "default") {
      Notification.requestPermission();
    }
  }, []);

  // Re-render every 100ms while a stage is running so the live timer ticks
  useEffect(() => {
    if (!loading) return;
    const id = setInterval(() => forceTick((t) => t + 1), 100);
    return () => clearInterval(id);
  }, [loading]);

  function startStage(key: StageKey) {
    setActiveStage(key);
    setStageTimes((prev) => ({ ...prev, [key]: { start: Date.now() } }));
  }

  function endStage(key: StageKey) {
    setStageTimes((prev) => {
      const existing = prev[key];
      if (!existing) return prev;
      return { ...prev, [key]: { ...existing, end: Date.now() } };
    });
  }

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

  function notifyDone(forTheme: string) {
    setNotice(`✅ Topics for "${forTheme}" are ready!`);
    setTimeout(() => setNotice(null), 6000);

    if (typeof Notification !== "undefined" && Notification.permission === "granted" && document.hidden) {
      const n = new Notification("Article Topic Generator", {
        body: `Topics for "${forTheme}" are ready!`,
      });
      n.onclick = () => {
        window.focus();
        n.close();
      };
    }
  }

  async function handleSignOut() {
    await supabase.auth.signOut();
    router.push("/login");
    router.refresh();
  }

  async function callStage(token: string, body: Record<string, unknown>) {
    const res = await fetch("/api/generate", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify(body),
    });

    const raw = await res.text();
    let data: any;
    try {
      data = JSON.parse(raw);
    } catch {
      throw new Error(
        res.status === 504 || /timeout/i.test(raw)
          ? "Generation timed out on the server - try again, or a shorter/simpler theme."
          : "The server returned an unexpected response. Please try again."
      );
    }

    if (!res.ok) {
      throw new Error(data.error || "Generation failed");
    }

    return data;
  }

  async function handleGenerate(e: React.FormEvent) {
    e.preventDefault();
    if (!theme) return;

    setLoading(true);
    setError(null);
    setStageTimes({});
    setActiveStage(null);
    setGenEnd(null);
    setGenStart(Date.now());

    const numTopics = Math.floor(Math.random() * 6) + 5; // 5-10, same as original

    try {
      const {
        data: { session },
      } = await supabase.auth.getSession();
      const token = session?.access_token ?? "";
      const base = { theme, num_topics: numTopics };

      startStage("plan");
      const { output: plannerOutput } = await callStage(token, {
        ...base,
        stage: "plan",
      });
      endStage("plan");

      startStage("research");
      const { output: researchOutput } = await callStage(token, {
        ...base,
        stage: "research",
        planner_output: plannerOutput,
      });
      endStage("research");

      startStage("condense");
      const [condenseResult, linksResult] = await Promise.all([
        callStage(token, {
          ...base,
          stage: "condense",
          research_output: researchOutput,
        }),
        callStage(token, {
          ...base,
          stage: "links",
          research_output: researchOutput,
        }),
      ]);
      endStage("condense");

      startStage("write");
      const { content } = await callStage(token, {
        ...base,
        stage: "write",
        planner_output: plannerOutput,
        condensed_output: condenseResult.output,
        links_output: linksResult.output,
      });
      endStage("write");

      // Save to history (RLS ensures this only ever writes the caller's own row)
      const { data: inserted } = await supabase
        .from("history")
        .insert({ theme, content, topic_count: numTopics })
        .select()
        .single();

      await loadHistory();
      // The freshly generated report becomes the one and only item shown
      // in the main panel; any previously expanded item is auto-minimized.
      if (inserted) setActiveId(inserted.id);
      notifyDone(theme);
    } catch (err: any) {
      setError(err.message || "Something went wrong");
    } finally {
      setLoading(false);
      setActiveStage(null);
      setGenEnd(Date.now());
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
    setActiveId((prev) => (prev === id ? null : prev));
  }

  // Acts like a switch: clicking the already-expanded item compresses it;
  // clicking a different item expands that one and auto-compresses the rest.
  function toggleActive(id: string) {
    setActiveId((prev) => (prev === id ? null : id));
    if (!loading) setTheme("");
  }

  return (
    <div className="layout">
      <div className="main-col">
        <div className="main-col-inner">
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
              {loading
                ? activeStage
                  ? `${STAGE_DEFS.find((s) => s.key === activeStage)?.icon} ${
                      STAGE_DEFS.find((s) => s.key === activeStage)?.label
                    } (${STAGE_DEFS.findIndex((s) => s.key === activeStage) + 1}/${STAGE_DEFS.length})...`
                  : "🔮 Working..."
                : "🚀 Generate Topics"}
            </button>
          </div>

          {Object.keys(stageTimes).length > 0 && (
            <div className="stage-tracker">
              {STAGE_DEFS.map((s) => {
                const info = stageTimes[s.key];
                const isActive = activeStage === s.key;
                const isDone = !!info?.end;
                const elapsedMs = info ? (info.end ?? Date.now()) - info.start : 0;
                return (
                  <div
                    key={s.key}
                    className={`stage-row${isActive ? " active" : ""}${isDone ? " done" : ""}`}
                  >
                    <span className="stage-icon">{isDone ? "✅" : isActive ? "⏳" : "○"}</span>
                    <span className="stage-label">
                      {s.icon} {s.label}
                    </span>
                    <span className="stage-time">{info ? formatDuration(elapsedMs) : ""}</span>
                  </div>
                );
              })}
              {!loading && genStart && genEnd && (
                <div className="stage-total">Total: {formatDuration(genEnd - genStart)}</div>
              )}
            </div>
          )}
        </form>

        {error && <div className="notification error">❌ {error}</div>}
        {notice && <div className="notification">{notice}</div>}

        {(() => {
          const activeItem = history.find((item) => item.id === activeId);
          if (!activeItem) return null;
          return (
            <div className="pinned-item">
              <div className="pinned-header">
                <div>
                  <strong>
                    📌 {activeItem.theme} ({activeItem.topic_count} topics)
                  </strong>
                  <p className="history-meta">
                    {new Date(activeItem.created_at).toLocaleString()}
                  </p>
                </div>
                <button onClick={() => toggleActive(activeItem.id)}>🗜️ Compress</button>
              </div>
              <div className="content-box">
                <ReactMarkdown>{activeItem.content}</ReactMarkdown>
              </div>
              <a
                className="download-link"
                onClick={() => downloadMarkdown(activeItem.content, activeItem.theme)}
              >
                📥 Download Topics
              </a>
            </div>
          );
        })()}

        <p style={{ marginTop: 40, opacity: 0.7 }}>
          🤖 Powered by Gemini 3.5 Flash
        </p>
        </div>
      </div>

      <div className="sidebar">
        <h2 style={{ fontSize: 16 }}>📚 Generation History</h2>
        {history.length === 0 && <p>No history yet</p>}
        {history.map((item) => {
          const isActive = activeId === item.id;
          return (
            <div className={`history-item${isActive ? " selected" : ""}`} key={item.id}>
              <strong>
                {item.theme} ({item.topic_count} topics)
              </strong>
              <p className="history-meta">{new Date(item.created_at).toLocaleString()}</p>

              <p className="history-preview">{item.content.slice(0, 160)}...</p>

              <div className="history-actions">
                <button onClick={() => toggleActive(item.id)}>
                  {isActive ? "🗜️ Compress" : "🔎 Expand"}
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
