import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import {
  AgentChatResponse,
  AgentChatResponseSchema,
  AgentStatus,
  AgentStatusSchema,
  PenpotUserRow,
  PenpotUsersResponseSchema,
  PgvectorVisionResponse,
  PgvectorVisionResponseSchema,
} from "./schemas";

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  trace?: AgentChatResponse["tool_trace"];
  latencyMs?: number;
}

const QUICK_ACTIONS = [
  { id: "vision_scan", label: "👁️ Ojos pgvector", prompt: "¿Qué ves actualmente en el lienzo de Penpot según pgvector?" },
  { id: "hero_card", label: "+ Hero Card", prompt: "Create a Hero Card component on the active page" },
  { id: "login_form", label: "+ Login Form", prompt: "Create a modern SSO Login Form card" },
  { id: "pricing_card", label: "+ Pricing Card", prompt: "Create an Enterprise AI Pricing Card" },
  { id: "navbar", label: "+ Navbar", prompt: "Create a responsive Navigation Bar header" },
  { id: "bind_tokens", label: "Bind Tokens", prompt: "Auto-bind library design tokens on the active page" },
  { id: "inspect_css", label: "Inspect CSS", prompt: "Extract CSS for the active selection or page shape" },
];

export default function App() {
  const [status, setStatus] = useState<AgentStatus | null>(null);
  const [wsConnected, setWsConnected] = useState(false);
  const [configOpen, setConfigOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<"routing" | "keys" | "users" | "inst">("routing");
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: "welcome",
      role: "assistant",
      text: "Penpot AI Studio v0.7.0 (React 19 + Zod · Python 3.14 + Pydantic · Go WebSocket Hub · Valkey 8.1 · PostgreSQL 17 + pgvector). Listo para diseñar.",
    },
  ]);
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);

  // Config & Users & pgvector state
  const [users, setUsers] = useState<PenpotUserRow[]>([]);
  const [visionQuery, setVisionQuery] = useState("Hero Pricing KPI Dark Card");
  const [visionResult, setVisionResult] = useState<PgvectorVisionResponse | null>(null);
  const [provider, setProvider] = useState("gemini");
  const [model, setModel] = useState("gemini-3.5-flash-lite");
  const [apiKeyName, setApiKeyName] = useState("custom-profile");
  const [apiKeyVal, setApiKeyVal] = useState("");
  const [toast, setToast] = useState("");

  const wsRef = useRef<WebSocket | null>(null);

  const refreshStatus = useCallback(async () => {
    try {
      const res = await fetch("/agent/status");
      const parsed = AgentStatusSchema.parse(await res.json());
      setStatus(parsed);
      setProvider(parsed.active_provider);
      setModel(parsed.default_model);
    } catch {
      /* ignore transient */
    }
  }, []);

  const refreshUsers = useCallback(async () => {
    try {
      const res = await fetch("/agent/users");
      const parsed = PenpotUsersResponseSchema.parse(await res.json());
      setUsers(parsed.users);
    } catch {
      /* ignore */
    }
  }, []);

  const runVisionScan = useCallback(async (q: string) => {
    try {
      const res = await fetch("/agent/vision", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: q }),
      });
      const parsed = PgvectorVisionResponseSchema.parse(await res.json());
      setVisionResult(parsed);
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    void refreshStatus();
    void refreshUsers();
    void runVisionScan("hero card");

    let cancelled = false;
    let ws: WebSocket | null = null;
    (async () => {
      try {
        const cfgRes = await fetch("/plugin/config.json");
        const cfg = (await cfgRes.json()) as { same_origin_ws_path?: string };
        if (cancelled) return;
        const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
        const wsPath = cfg.same_origin_ws_path || "/plugin/ws";
        ws = new WebSocket(`${proto}//${window.location.host}${wsPath}`);
        wsRef.current = ws;
        ws.onopen = () => setWsConnected(true);
        ws.onclose = () => setWsConnected(false);
        ws.onerror = () => setWsConnected(false);
      } catch {
        setWsConnected(false);
      }
    })();

    return () => {
      cancelled = true;
      ws?.close();
    };
  }, [refreshStatus, refreshUsers, runVisionScan]);

  const sendPrompt = async (text: string, quickAction?: string) => {
    const trimmed = text.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setMessages((prev) => [...prev, { id: `u-${Date.now()}`, role: "user", text: trimmed }]);
    setPrompt("");
    try {
      const res = await fetch("/agent/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: trimmed, quick_action: quickAction }),
      });
      const parsed = AgentChatResponseSchema.parse(await res.json());
      setMessages((prev) => [
        ...prev,
        {
          id: `a-${Date.now()}`,
          role: "assistant",
          text: parsed.reply || `Estado: ${parsed.status}`,
          trace: parsed.tool_trace,
          latencyMs: parsed.latency_ms,
        },
      ]);
      void refreshStatus();
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { id: `e-${Date.now()}`, role: "assistant", text: `Error Zod/Gateway: ${String(err)}` },
      ]);
    } finally {
      setBusy(false);
    }
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    void sendPrompt(prompt);
  };

  const switchUser = async (u: PenpotUserRow) => {
    await fetch("/agent/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: u.id, email: u.email }),
    });
    setToast(`Sesión activa cambiada a ${u.fullname}`);
    await refreshStatus();
    await refreshUsers();
  };

  const saveProfile = async () => {
    await fetch("/agent/profile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider, model, preset: "auto" }),
    });
    setToast(`Perfil actualizado: ${provider}:${model}`);
    await refreshStatus();
  };

  const saveKey = async () => {
    if (!apiKeyVal.trim()) return;
    await fetch("/agent/providers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: apiKeyName,
        provider,
        api_key: apiKeyVal.trim(),
        model,
        activate: true,
      }),
    });
    setApiKeyVal("");
    setToast(`API Key guardada y activada para ${provider}`);
    await refreshStatus();
  };

  return (
    <div className="app-shell">
      <header className="header">
        <div className="header-top">
          <div className="brand">
            <span className="brand-badge">React + Zod</span>
            <span>Penpot AI Studio v0.7.0</span>
          </div>
          <div style={{ display: "flex", gap: "6px" }}>
            <button className="btn" type="button" onClick={() => setConfigOpen((v) => !v)}>
              {configOpen ? "✕ Volver al Chat" : "⚙️ Config"}
            </button>
            <button className="btn" type="button" onClick={() => void refreshStatus()}>
              Sync
            </button>
          </div>
        </div>
        <div className="badges-row">
          <span className="badge ok">👤 {status?.active_user?.fullname ?? "Penpot User"}</span>
          <span className="badge">{status?.active_provider}:{status?.default_model}</span>
          <span className={`badge ${wsConnected ? "ok" : ""}`}>
            🔌 Go WS: {wsConnected ? "Live" : "Offline"}
          </span>
          <span className="badge ok">
            👁️ pgvector {status?.pgvector?.pgvector_version || "0.8.7"} ({status?.pgvector?.indexed_shapes_count ?? 0})
          </span>
          <span className="badge">
            ⚡ Valkey {status?.valkey_cache?.hits ?? 0}H/{status?.valkey_cache?.misses ?? 0}M
          </span>
        </div>
      </header>

      {configOpen ? (
        <section className="drawer">
          {toast && <div className="badge ok">{toast}</div>}
          <div className="tabs">
            <button className={`tab ${activeTab === "routing" ? "active" : ""}`} onClick={() => setActiveTab("routing")}>
              1. Agente / Perfil
            </button>
            <button className={`tab ${activeTab === "keys" ? "active" : ""}`} onClick={() => setActiveTab("keys")}>
              2. API Keys
            </button>
            <button className={`tab ${activeTab === "users" ? "active" : ""}`} onClick={() => setActiveTab("users")}>
              3. Usuarios & pgvector
            </button>
            <button className={`tab ${activeTab === "inst" ? "active" : ""}`} onClick={() => setActiveTab("inst")}>
              4. Arquitectura
            </button>
          </div>

          {activeTab === "routing" && (
            <div className="card">
              <strong>Selector de Proveedor y Modelo (Pydantic + Zod)</strong>
              <select className="input" value={provider} onChange={(e) => setProvider(e.target.value)}>
                <option value="gemini">Google Gemini</option>
                <option value="anthropic">Anthropic Claude</option>
                <option value="openai">OpenAI ChatGPT</option>
                <option value="deepseek">DeepSeek</option>
                <option value="ollama">Ollama Local</option>
              </select>
              <input className="input" value={model} onChange={(e) => setModel(e.target.value)} placeholder="Modelo activo" />
              <button className="btn btn-primary" type="button" onClick={() => void saveProfile()}>
                ✓ Aplicar Perfil
              </button>
            </div>
          )}

          {activeTab === "keys" && (
            <div className="card">
              <strong>Bóveda de API Keys por Usuario</strong>
              <input className="input" value={apiKeyName} onChange={(e) => setApiKeyName(e.target.value)} placeholder="Nombre de perfil" />
              <input className="input" type="password" value={apiKeyVal} onChange={(e) => setApiKeyVal(e.target.value)} placeholder="sk-... / AIza..." />
              <button className="btn btn-primary" type="button" onClick={() => void saveKey()}>
                🔑 Guardar / Reemplazar Key
              </button>
            </div>
          )}

          {activeTab === "users" && (
            <>
              <div className="card">
                <strong>👥 Usuarios Autenticados en Penpot (public.profile)</strong>
                {users.map((u) => (
                  <div key={u.id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "8px" }}>
                    <div>
                      <div style={{ fontSize: "12px", fontWeight: 600 }}>{u.fullname}</div>
                      <div style={{ fontSize: "11px", color: "var(--text-1)" }}>{u.email} · Sesiones: {u.active_sessions}</div>
                    </div>
                    <button className="btn btn-primary" type="button" onClick={() => void switchUser(u)}>
                      {status?.active_user?.email === u.email ? "✓ Activo" : "Cambiar"}
                    </button>
                  </div>
                ))}
              </div>
              <div className="card">
                <strong>👁️ Escáner Semántico & Espacial (pgvector + Valkey)</strong>
                <div style={{ display: "flex", gap: "6px" }}>
                  <input className="input" value={visionQuery} onChange={(e) => setVisionQuery(e.target.value)} />
                  <button className="btn btn-primary" type="button" onClick={() => void runVisionScan(visionQuery)}>
                    🔍 Escanear
                  </button>
                </div>
                <div className="code-box">{visionResult?.prompt_summary || "Sin resultados"}</div>
              </div>
            </>
          )}

          {activeTab === "inst" && (
            <div className="card">
              <strong>Stack Verificado v0.7.0</strong>
              <div className="code-box">
                {`Frontend: React 19 + TypeScript + Zod (Nginx Unprivileged :8787)\nWebSockets: Go 1.24 Hub (/plugin/ws -> penpot-ws-go:4402)\nBackend MCP: Python 3.14 + FastMCP + Pydantic v2 (:8787 internal)\nCache: Valkey 8.1 (volatile-lfu, O(1) epoch invalidation)\nVector DB: PostgreSQL 17.12 + pgvector 0.8.7 + pg_trgm 1.6`}
              </div>
            </div>
          )}
        </section>
      ) : (
        <>
          <div className="quick-strip">
            {QUICK_ACTIONS.map((qa) => (
              <button key={qa.id} className="chip" type="button" onClick={() => void sendPrompt(qa.prompt, qa.id)}>
                {qa.label}
              </button>
            ))}
          </div>

          <main className="feed">
            {messages.map((m) => (
              <div key={m.id} className={`msg ${m.role}`}>
                <div>{m.text}</div>
                {m.trace && m.trace.length > 0 && (
                  <div className="trace-pill">
                    ⚡ {m.trace.map((t) => `${t.tool}.${t.action} (${t.status})`).join(" · ")}
                    {m.latencyMs ? ` · ${m.latencyMs.toFixed(1)} ms` : ""}
                  </div>
                )}
              </div>
            ))}
          </main>

          <form className="composer" onSubmit={handleSubmit}>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="Describe el componente o acción en Penpot..."
            />
            <button className="btn btn-primary" type="submit" disabled={busy}>
              {busy ? "..." : "Generar"}
            </button>
          </form>
        </>
      )}
    </div>
  );
}
