import { ChangeEvent, DragEvent, FormEvent, KeyboardEvent, useCallback, useEffect, useId, useRef, useState } from "react";
import {
  AgentChatResponse,
  AgentChatResponseSchema,
  AgentStatus,
  AgentStatusSchema,
  ChatSessionListSchema,
  ChatSessionSummary,
  PenpotUserRow,
  PenpotUsersResponseSchema,
  PgvectorVisionResponse,
  PgvectorVisionResponseSchema,
  UploadedAttachment,
  UploadResponseSchema,
} from "./schemas";

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  attachments?: UploadedAttachment[];
  trace?: AgentChatResponse["tool_trace"];
  createdShapes?: AgentChatResponse["created_shapes"];
  artifacts?: AgentChatResponse["artifacts"];
  latencyMs?: number;
  timestamp: string;
}

type StudioTab = "chat" | "routing" | "keys" | "users" | "inst";

const TABS: { id: StudioTab; label: string; shortLabel: string }[] = [
  { id: "chat", label: "💬 Studio Chat", shortLabel: "Chat" },
  { id: "routing", label: "🤖 Agente / Perfil", shortLabel: "Agente" },
  { id: "keys", label: "🔑 API Keys", shortLabel: "Keys" },
  { id: "users", label: "👥 Usuarios & pgvector", shortLabel: "Usuarios" },
  { id: "inst", label: "🏗️ Arquitectura", shortLabel: "Stack" },
];

const QUICK_ACTIONS = [
  { id: "vision_scan", label: "👁️ Ojos pgvector", prompt: "¿Qué ves actualmente en el lienzo de Penpot según pgvector?" },
  { id: "hero_card", label: "+ Hero Card", prompt: "Create a Hero Card component on the active page" },
  { id: "login_form", label: "+ Login Form", prompt: "Create a modern SSO Login Form card" },
  { id: "pricing_card", label: "+ Pricing Card", prompt: "Create an Enterprise AI Pricing Card" },
  { id: "navbar", label: "+ Navbar", prompt: "Create a responsive Navigation Bar header" },
  { id: "bind_tokens", label: "Bind Tokens", prompt: "Auto-bind library design tokens on the active page" },
  { id: "inspect_css", label: "Inspect CSS", prompt: "Extract CSS for the active selection or page shape" },
];

const PROVIDER_MODELS: Record<string, { label: string; models: string[] }> = {
  gemini: {
    label: "Google Gemini",
    models: ["gemini-3.5-flash-lite", "gemini-2.5-flash", "gemini-2.5-pro"],
  },
  anthropic: {
    label: "Anthropic Claude",
    models: ["claude-sonnet-4-20250514", "claude-3-7-sonnet-latest", "claude-3-5-haiku-latest"],
  },
  openai: {
    label: "OpenAI ChatGPT",
    models: ["gpt-4.1", "gpt-4.1-mini", "o4-mini"],
  },
  deepseek: {
    label: "DeepSeek",
    models: ["deepseek-chat", "deepseek-reasoner"],
  },
  ollama: {
    label: "Ollama Local",
    models: ["qwen2.5-coder:14b", "llama3.2:latest", "deepseek-r1:8b"],
  },
};

function nowLabel(): string {
  return new Date().toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit" });
}

export default function App() {
  const baseId = useId();
  const [status, setStatus] = useState<AgentStatus | null>(null);
  const [wsConnected, setWsConnected] = useState(false);
  const [activeTab, setActiveTab] = useState<StudioTab>("chat");
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: "welcome",
      role: "assistant",
      text: "Penpot AI Studio v0.7.0 listo. Conectado al Gateway MCP (Python 3.14 + Pydantic), Go WebSocket Hub, Valkey 8.1 y PostgreSQL 17 + pgvector.",
      timestamp: nowLabel(),
    },
  ]);
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [attachments, setAttachments] = useState<UploadedAttachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [importPenpot, setImportPenpot] = useState(false);
  const [sandboxPersistent, setSandboxPersistent] = useState(false);
  const [chatSessions, setChatSessions] = useState<ChatSessionSummary[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string>(() => {
    try {
      return window.localStorage.getItem("penpot_ai_session_id") || "default";
    } catch {
      return "default";
    }
  });
  const [dragActive, setDragActive] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // Config & Users & pgvector state
  const [users, setUsers] = useState<PenpotUserRow[]>([]);
  const [visionQuery, setVisionQuery] = useState("Hero Pricing KPI Dark Card");
  const [visionResult, setVisionResult] = useState<PgvectorVisionResponse | null>(null);
  const [visionLoading, setVisionLoading] = useState(false);
  const [provider, setProvider] = useState("gemini");
  const [model, setModel] = useState("gemini-3.5-flash-lite");
  const [apiKeyName, setApiKeyName] = useState("custom-profile");
  const [apiKeyVal, setApiKeyVal] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [keyError, setKeyError] = useState("");
  const [toast, setToast] = useState("");

  const wsRef = useRef<WebSocket | null>(null);
  const feedEndRef = useRef<HTMLDivElement | null>(null);
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const keyInputRef = useRef<HTMLInputElement | null>(null);

  const showToast = useCallback((msg: string) => {
    setToast(msg);
  }, []);

  useEffect(() => {
    if (!toast) return;
    const t = window.setTimeout(() => setToast(""), 4000);
    return () => window.clearTimeout(t);
  }, [toast]);

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
    setVisionLoading(true);
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
    } finally {
      setVisionLoading(false);
    }
  }, []);

  const refreshChatList = useCallback(async () => {
    try {
      const res = await fetch("/agent/chats");
      const parsed = ChatSessionListSchema.parse(await res.json());
      setChatSessions(parsed.sessions);
    } catch {
      /* ignore */
    }
  }, []);

  const loadChatSession = useCallback(async (sid: string) => {
    try {
      window.localStorage.setItem("penpot_ai_session_id", sid);
    } catch {
      /* ignore */
    }
    setActiveSessionId(sid);
    try {
      const res = await fetch(`/agent/chats/${encodeURIComponent(sid)}`);
      const data = (await res.json()) as {
        session?: { messages?: ChatMessage[]; sandbox_persistent?: boolean };
        sandbox?: { persistent?: boolean };
      };
      const loadedMsgs = data.session?.messages;
      if (Array.isArray(loadedMsgs) && loadedMsgs.length > 0) {
        setMessages(loadedMsgs);
      } else {
        setMessages([
          {
            id: "welcome",
            role: "assistant",
            text: "Nueva sesión lista. Tu historial y configuración de sandbox quedan guardados en PostgreSQL.",
            timestamp: nowLabel(),
          },
        ]);
      }
      setSandboxPersistent(Boolean(data.sandbox?.persistent ?? data.session?.sandbox_persistent));
    } catch {
      /* ignore */
    }
  }, []);

  const createNewChat = async () => {
    const newSid = `chat-${Date.now().toString(36)}`;
    await fetch(`/agent/chats/${encodeURIComponent(newSid)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: "Nuevo chat", messages: [], sandbox_persistent: sandboxPersistent }),
    });
    await refreshChatList();
    await loadChatSession(newSid);
    setToast(`Nuevo chat creado (${newSid})`);
  };

  useEffect(() => {
    void refreshStatus();
    void refreshUsers();
    void runVisionScan("hero card");
    void refreshChatList();
    void loadChatSession(activeSessionId);

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

  useEffect(() => {
    feedEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, busy]);

  const uploadFiles = async (fileList: FileList | null) => {
    if (!fileList || fileList.length === 0) return;
    setUploading(true);
    try {
      for (const file of Array.from(fileList)) {
        const fd = new FormData();
        fd.append("file", file);
        if (importPenpot && file.name.toLowerCase().endsWith(".penpot")) {
          fd.append("import_to_penpot", "true");
        }
        const res = await fetch("/agent/upload", { method: "POST", body: fd });
        const parsed = UploadResponseSchema.parse(await res.json());
        if (parsed.status === "OK" && parsed.attachment) {
          const att = parsed.attachment;
          setAttachments((prev) => [...prev, att]);
          showToast(
            att.imported_to_penpot
              ? `Plantilla ${att.filename} importada a Penpot y adjuntada al chat`
              : `Adjuntado al contexto: ${att.filename}`
          );
        } else {
          showToast(`Error al adjuntar ${file.name}: ${parsed.error || "formato inválido"}`);
        }
      }
    } catch (err) {
      showToast(`Error subiendo archivo: ${String(err)}`);
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const handleFileChange = (e: ChangeEvent<HTMLInputElement>) => {
    void uploadFiles(e.target.files);
  };

  const handleDrop = (e: DragEvent<HTMLElement>) => {
    e.preventDefault();
    setDragActive(false);
    void uploadFiles(e.dataTransfer.files);
  };

  const sendPrompt = async (text: string, quickAction?: string) => {
    const trimmed = text.trim();
    if ((!trimmed && attachments.length === 0) || busy) return;
    const activeAttachments = [...attachments];
    const effectiveText = trimmed || `Analiza los archivos adjuntos (${activeAttachments.map((a) => a.filename).join(", ")}).`;
    setActiveTab("chat");
    setBusy(true);
    setMessages((prev) => [
      ...prev,
      {
        id: `u-${Date.now()}`,
        role: "user",
        text: effectiveText,
        attachments: activeAttachments,
        timestamp: nowLabel(),
      },
    ]);
    setPrompt("");
    setAttachments([]);
    try {
      const res = await fetch("/agent/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          prompt: effectiveText,
          quick_action: quickAction,
          session_id: activeSessionId,
          attachment_ids: activeAttachments.map((a) => a.id),
          sandbox_persistent: sandboxPersistent,
        }),
      });
      const parsed = AgentChatResponseSchema.parse(await res.json());
      setMessages((prev) => [
        ...prev,
        {
          id: `a-${Date.now()}`,
          role: "assistant",
          text: parsed.reply || `Estado: ${parsed.status}`,
          trace: parsed.tool_trace,
          createdShapes: parsed.created_shapes,
          artifacts: parsed.artifacts,
          latencyMs: parsed.latency_ms,
          timestamp: nowLabel(),
        },
      ]);
      void refreshStatus();
      void refreshChatList();
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          id: `e-${Date.now()}`,
          role: "assistant",
          text: `Error Zod/Gateway: ${String(err)}`,
          timestamp: nowLabel(),
        },
      ]);
    } finally {
      setBusy(false);
    }
  };

  const handleChatSubmit = (e: FormEvent) => {
    e.preventDefault();
    void sendPrompt(prompt);
  };

  const handleComposerKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void sendPrompt(prompt);
    }
  };

  const switchUser = async (u: PenpotUserRow) => {
    await fetch("/agent/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: u.id, email: u.email }),
    });
    showToast(`Sesión activa cambiada a ${u.fullname}`);
    await refreshStatus();
    await refreshUsers();
  };

  const handleProviderChange = (nextProvider: string) => {
    setProvider(nextProvider);
    const presets = PROVIDER_MODELS[nextProvider]?.models;
    if (presets && presets.length > 0) {
      setModel(presets[0]);
    }
  };

  const saveProfile = async (e: FormEvent) => {
    e.preventDefault();
    await fetch("/agent/profile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider, model: model.trim(), preset: "auto" }),
    });
    showToast(`Perfil actualizado: ${provider}:${model}`);
    await refreshStatus();
  };

  const saveKey = async (e: FormEvent) => {
    e.preventDefault();
    if (!apiKeyVal.trim()) {
      setKeyError("Ingresa una API Key válida antes de guardar.");
      keyInputRef.current?.focus();
      return;
    }
    setKeyError("");
    await fetch("/agent/providers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: apiKeyName.trim() || "custom-profile",
        provider,
        api_key: apiKeyVal.trim(),
        model: model.trim(),
        activate: true,
      }),
    });
    setApiKeyVal("");
    showToast(`API Key guardada y activada para ${provider}`);
    await refreshStatus();
  };

  const handleVisionSubmit = (e: FormEvent) => {
    e.preventDefault();
    void runVisionScan(visionQuery);
  };

  const handleTabKeyDown = (e: KeyboardEvent<HTMLButtonElement>, idx: number) => {
    let nextIdx = idx;
    if (e.key === "ArrowRight") nextIdx = (idx + 1) % TABS.length;
    else if (e.key === "ArrowLeft") nextIdx = (idx - 1 + TABS.length) % TABS.length;
    else if (e.key === "Home") nextIdx = 0;
    else if (e.key === "End") nextIdx = TABS.length - 1;
    else return;
    e.preventDefault();
    const nextTab = TABS[nextIdx];
    setActiveTab(nextTab.id);
    tabRefs.current[nextIdx]?.focus();
  };

  const valkeyHits = status?.valkey_cache?.hits ?? 0;
  const valkeyMisses = status?.valkey_cache?.misses ?? 0;
  const valkeyTotal = valkeyHits + valkeyMisses;
  const valkeyHitRate = valkeyTotal > 0 ? Math.round((valkeyHits / valkeyTotal) * 100) : 0;

  return (
    <div className="app-shell">
      <header className="header">
        <div className="header-top">
          <div className="brand">
            <span className="brand-badge">React 19 + Zod</span>
            <h1 className="brand-title">Penpot AI Studio <span className="version-tag">v0.7.0</span></h1>
          </div>
          <div className="header-actions">
            <button
              className="btn btn-ghost"
              type="button"
              onClick={() => void refreshStatus()}
              title="Sincronizar estado del gateway"
            >
              ↻ Sync
            </button>
          </div>
        </div>

        <div className="badges-row" aria-label="Telemetría en vivo">
          <span className="badge ok">👤 {status?.active_user?.fullname ?? "Penpot User"}</span>
          <span className="badge accent">
            🤖 {status?.active_provider ?? provider}:{status?.default_model ?? model}
          </span>
          <span className={`badge ${wsConnected ? "ok" : "warn"}`}>
            🔌 Go WS: {wsConnected ? "Live" : "Offline"}
          </span>
          <span className="badge ok">
            👁️ pgvector {status?.pgvector?.pgvector_version || "0.8.7"} ({status?.pgvector?.indexed_shapes_count ?? 0})
          </span>
          <span className="badge">
            ⚡ Valkey {valkeyHits}H/{valkeyMisses}M
          </span>
        </div>

        <nav className="tablist-wrap" aria-label="Navegación del estudio">
          <div className="tablist" role="tablist" aria-label="Secciones de Penpot AI Studio">
            {TABS.map((t, idx) => {
              const selected = activeTab === t.id;
              return (
                <button
                  key={t.id}
                  ref={(el) => {
                    tabRefs.current[idx] = el;
                  }}
                  id={`${baseId}-tab-${t.id}`}
                  type="button"
                  role="tab"
                  aria-selected={selected}
                  tabIndex={selected ? 0 : -1}
                  className="tab-btn"
                  onClick={() => setActiveTab(t.id)}
                  onKeyDown={(e) => handleTabKeyDown(e, idx)}
                >
                  {t.label}
                </button>
              );
            })}
          </div>
        </nav>
      </header>

      {toast && (
        <div className="toast-banner" role="status" aria-live="polite">
          <span>✓ {toast}</span>
          <button type="button" className="toast-close" onClick={() => setToast("")} aria-label="Cerrar notificación">
            ✕
          </button>
        </div>
      )}

      <main className="main-content">
        {/* TAB 0: STUDIO CHAT */}
        <section
          role="tabpanel"
          id={`${baseId}-panel-chat`}
          aria-labelledby={`${baseId}-tab-chat`}
          hidden={activeTab !== "chat"}
          className={`panel-chat ${dragActive ? "drop-active" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragActive(true);
          }}
          onDragLeave={() => setDragActive(false)}
          onDrop={handleDrop}
        >
          <div className="chat-sessions-bar">
            <label htmlFor={`${baseId}-chat-select`} className="visually-hidden">
              Conversación guardada
            </label>
            <select
              id={`${baseId}-chat-select`}
              className="chat-select"
              value={activeSessionId}
              onChange={(e) => void loadChatSession(e.target.value)}
            >
              {chatSessions.length === 0 && <option value={activeSessionId}>Sesión principal</option>}
              {chatSessions.map((cs) => (
                <option key={cs.session_id} value={cs.session_id}>
                  {cs.sandbox_persistent ? "🔒 " : ""}
                  {cs.title} ({cs.message_count} msgs)
                </option>
              ))}
            </select>
            <button type="button" className="btn btn-ghost btn-nowrap" onClick={() => void createNewChat()}>
              + Nuevo Chat
            </button>
          </div>

          <div className="quick-strip" aria-label="Acciones rápidas de diseño">
            {QUICK_ACTIONS.map((qa) => (
              <button
                key={qa.id}
                className="chip"
                type="button"
                disabled={busy}
                onClick={() => void sendPrompt(qa.prompt, qa.id)}
              >
                {qa.label}
              </button>
            ))}
          </div>

          <div className="feed">
            {messages.length === 1 && (
              <div className="studio-context-banner">
                <div className="context-item">
                  <span className="context-label">Plugin Penpot</span>
                  <strong className="context-val">
                    {status?.plugin?.connected ? "Conectado" : "Listo (esperando canvas)"}
                  </strong>
                </div>
                <div className="context-item">
                  <span className="context-label">Archivo / Página</span>
                  <strong className="context-val">
                    {status?.plugin?.file_name || "Proyecto activo"} · {status?.plugin?.page_name || "Page 1"}
                  </strong>
                </div>
                <div className="context-item">
                  <span className="context-label">Índice Espacial</span>
                  <strong className="context-val">
                    {visionResult?.total_shapes ?? status?.pgvector?.indexed_shapes_count ?? 0} shapes indexados
                  </strong>
                </div>
              </div>
            )}

            {messages.map((m) => (
              <article key={m.id} className={`msg ${m.role}`}>
                <div className="msg-meta">
                  <span className="msg-author">{m.role === "user" ? "Tú" : "Penpot AI"}</span>
                  <span className="msg-time">{m.timestamp}</span>
                </div>
                <div className="msg-body">{m.text}</div>
                {m.attachments && m.attachments.length > 0 && (
                  <div className="shapes-row">
                    {m.attachments.map((att) => (
                      <span key={att.id} className="attach-pill">
                        📎 {att.filename} ({att.kind})
                      </span>
                    ))}
                  </div>
                )}
                {m.createdShapes && m.createdShapes.length > 0 && (
                  <div className="shapes-row">
                    {m.createdShapes.map((s) => (
                      <span key={s.shape_id} className="shape-pill">
                        ◆ {s.name} ({s.type})
                      </span>
                    ))}
                  </div>
                )}
                {m.artifacts && m.artifacts.length > 0 && (
                  <div className="shapes-row">
                    {m.artifacts.map((art) => (
                      <a
                        key={art.path}
                        className="artifact-pill"
                        href={art.download_url}
                        download={art.name}
                      >
                        ⬇ {art.name}
                      </a>
                    ))}
                  </div>
                )}
                {m.trace && m.trace.length > 0 && (
                  <div className="trace-pill">
                    ⚡ {m.trace.map((t) => `${t.tool}.${t.action} (${t.status})`).join(" · ")}
                    {m.latencyMs ? ` · ${m.latencyMs.toFixed(1)} ms` : ""}
                  </div>
                )}
              </article>
            ))}

            {busy && (
              <div className="msg assistant typing-indicator" aria-live="polite">
                Ejecutando herramientas en Penpot MCP...
              </div>
            )}
            <div ref={feedEndRef} />
          </div>

          {attachments.length > 0 && (
            <div className="attach-bar" aria-label="Archivos adjuntos al contexto">
              {attachments.map((att) => (
                <span key={att.id} className="attach-chip">
                  <span>
                    📎 {att.filename} · {att.summary || att.kind}
                  </span>
                  <button
                    type="button"
                    className="attach-remove"
                    aria-label={`Quitar ${att.filename}`}
                    onClick={() => setAttachments((prev) => prev.filter((x) => x.id !== att.id))}
                  >
                    ✕
                  </button>
                </span>
              ))}
            </div>
          )}

          <form className="composer" onSubmit={handleChatSubmit}>
            <input
              ref={fileInputRef}
              id={`${baseId}-file-upload`}
              type="file"
              className="visually-hidden"
              multiple
              accept=".penpot,.svg,.json,.md,.txt,.css,.html,.csv,.yaml,.yml,.pdf,.docx,.xlsx,.png,.jpg,.jpeg,.webp"
              onChange={handleFileChange}
            />
            <div className="composer-input-wrap">
              <div className="composer-toolbar">
                <button
                  type="button"
                  className="btn btn-ghost btn-nowrap"
                  disabled={uploading || busy}
                  onClick={() => fileInputRef.current?.click()}
                >
                  {uploading ? "Subiendo..." : "📎 Adjuntar (.penpot / SVG / JSON / Img)"}
                </button>
                <label className="import-toggle" htmlFor={`${baseId}-import-penpot`}>
                  <input
                    id={`${baseId}-import-penpot`}
                    type="checkbox"
                    checked={importPenpot}
                    onChange={(e) => setImportPenpot(e.target.checked)}
                  />
                  <span>Importar .penpot al proyecto</span>
                </label>
                <label className="import-toggle" htmlFor={`${baseId}-sandbox-persistent`}>
                  <input
                    id={`${baseId}-sandbox-persistent`}
                    type="checkbox"
                    checked={sandboxPersistent}
                    onChange={(e) => {
                      const next = e.target.checked;
                      setSandboxPersistent(next);
                      void fetch("/agent/sandbox/policy", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ session_id: activeSessionId, persistent: next }),
                      });
                      void fetch(`/agent/chats/${encodeURIComponent(activeSessionId)}`, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ sandbox_persistent: next }),
                      });
                      setToast(
                        next
                          ? "Sandbox sin expiración activado (sesión larga)"
                          : "Sandbox en modo efímero (TTL 30 min)",
                      );
                    }}
                  />
                  <span>🔒 Sandbox sin expiración (sesión larga)</span>
                </label>
              </div>
              <label htmlFor={`${baseId}-prompt`} className="visually-hidden">
                Instrucción para el agente de Penpot
              </label>
              <textarea
                id={`${baseId}-prompt`}
                name="prompt"
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                onKeyDown={handleComposerKeyDown}
                placeholder="Describe el componente o suelta una plantilla .penpot / SVG / imagen aquí... (Enter para enviar)"
                rows={2}
              />
              <span className="composer-hint">Enter enviar · Shift+Enter nueva línea · Arrastra archivos para adjuntar</span>
            </div>
            <button
              className="btn btn-primary btn-send"
              type="submit"
              disabled={busy || uploading || (!prompt.trim() && attachments.length === 0)}
            >
              {busy ? "Procesando..." : "Generar →"}
            </button>
          </form>
        </section>

        {/* TAB 1: AGENTE / PERFIL */}
        <section
          role="tabpanel"
          id={`${baseId}-panel-routing`}
          aria-labelledby={`${baseId}-tab-routing`}
          hidden={activeTab !== "routing"}
          className="panel-scroll"
        >
          <div className="panel-inner">
            <form className="card" onSubmit={(e) => void saveProfile(e)} aria-labelledby={`${baseId}-routing-title`}>
              <div className="card-head">
                <h2 id={`${baseId}-routing-title`} className="card-title">
                  Selector de Proveedor y Modelo
                </h2>
                <span className="badge accent">Pydantic + Zod</span>
              </div>
              <p className="card-desc">
                Configura el motor de IA activo para la sesión de{" "}
                <strong>{status?.active_user?.fullname ?? "Penpot User"}</strong>.
              </p>

              <div className="field">
                <label htmlFor={`${baseId}-provider`}>Proveedor de IA</label>
                <select
                  id={`${baseId}-provider`}
                  name="provider"
                  className="input"
                  value={provider}
                  onChange={(e) => handleProviderChange(e.target.value)}
                >
                  {Object.entries(PROVIDER_MODELS).map(([key, info]) => (
                    <option key={key} value={key}>
                      {info.label}
                    </option>
                  ))}
                </select>
              </div>

              <div className="field">
                <label htmlFor={`${baseId}-model`}>Modelo activo</label>
                <input
                  id={`${baseId}-model`}
                  name="model"
                  className="input"
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  placeholder="Ej. gemini-3.5-flash-lite"
                  autoCorrect="off"
                  autoCapitalize="none"
                  spellCheck={false}
                  required
                />
                <div className="preset-chips" aria-label="Modelos recomendados">
                  {(PROVIDER_MODELS[provider]?.models ?? []).map((presetModel) => (
                    <button
                      key={presetModel}
                      type="button"
                      className={`chip ${model === presetModel ? "chip-selected" : ""}`}
                      onClick={() => setModel(presetModel)}
                    >
                      {presetModel}
                    </button>
                  ))}
                </div>
              </div>

              <div className="card-actions">
                <button className="btn btn-primary" type="submit">
                  ✓ Aplicar Perfil
                </button>
              </div>
            </form>
          </div>
        </section>

        {/* TAB 2: API KEYS */}
        <section
          role="tabpanel"
          id={`${baseId}-panel-keys`}
          aria-labelledby={`${baseId}-tab-keys`}
          hidden={activeTab !== "keys"}
          className="panel-scroll"
        >
          <div className="panel-inner">
            <form className="card" onSubmit={(e) => void saveKey(e)} noValidate aria-labelledby={`${baseId}-keys-title`}>
              <div className="card-head">
                <h2 id={`${baseId}-keys-title`} className="card-title">
                  Bóveda de API Keys por Usuario
                </h2>
                <span className="badge ok">👤 {status?.active_user?.fullname ?? "Penpot User"}</span>
              </div>
              <p className="card-desc">
                Guarda o reemplaza la credencial asociada a tu perfil de Penpot sin reiniciar contenedores.
              </p>

              <div className="field-grid-2">
                <div className="field">
                  <label htmlFor={`${baseId}-key-provider`}>Proveedor destino</label>
                  <select
                    id={`${baseId}-key-provider`}
                    name="key_provider"
                    className="input"
                    value={provider}
                    onChange={(e) => handleProviderChange(e.target.value)}
                  >
                    {Object.entries(PROVIDER_MODELS).map(([key, info]) => (
                      <option key={key} value={key}>
                        {info.label}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="field">
                  <label htmlFor={`${baseId}-key-name`}>Nombre de perfil</label>
                  <input
                    id={`${baseId}-key-name`}
                    name="profile_name"
                    className="input"
                    value={apiKeyName}
                    onChange={(e) => setApiKeyName(e.target.value)}
                    placeholder="custom-profile"
                    autoCorrect="off"
                    autoCapitalize="none"
                    spellCheck={false}
                    required
                  />
                </div>
              </div>

              <div className="field">
                <label htmlFor={`${baseId}-key-val`}>API Key</label>
                <div className="password-row">
                  <input
                    ref={keyInputRef}
                    id={`${baseId}-key-val`}
                    name="api_key"
                    className="input"
                    type={showKey ? "text" : "password"}
                    value={apiKeyVal}
                    onChange={(e) => {
                      setApiKeyVal(e.target.value);
                      if (keyError) setKeyError("");
                    }}
                    placeholder="sk-... / AIza..."
                    autoComplete="off"
                    autoCorrect="off"
                    autoCapitalize="none"
                    spellCheck={false}
                    aria-invalid={Boolean(keyError)}
                    aria-describedby={keyError ? `${baseId}-key-err` : `${baseId}-key-hint`}
                  />
                  <button
                    type="button"
                    className="btn btn-ghost btn-nowrap"
                    aria-pressed={showKey}
                    aria-label={showKey ? "Ocultar API Key" : "Mostrar API Key"}
                    onClick={() => setShowKey((v) => !v)}
                  >
                    {showKey ? "🙈 Ocultar" : "👁️ Ver"}
                  </button>
                </div>
                {keyError ? (
                  <span id={`${baseId}-key-err`} className="field-error" role="alert">
                    {keyError}
                  </span>
                ) : (
                  <span id={`${baseId}-key-hint`} className="field-hint">
                    Se almacenará vinculada al usuario activo y se activará inmediatamente para {provider}.
                  </span>
                )}
              </div>

              <div className="card-actions">
                <button className="btn btn-primary" type="submit">
                  🔑 Guardar / Reemplazar Key
                </button>
              </div>
            </form>
          </div>
        </section>

        {/* TAB 3: USUARIOS & PGVECTOR */}
        <section
          role="tabpanel"
          id={`${baseId}-panel-users`}
          aria-labelledby={`${baseId}-tab-users`}
          hidden={activeTab !== "users"}
          className="panel-scroll"
        >
          <div className="panel-inner">
            <div className="card">
              <div className="card-head">
                <h2 className="card-title">👥 Usuarios Autenticados en Penpot</h2>
                <span className="badge">public.profile ({users.length})</span>
              </div>
              <div className="user-list">
                {users.map((u) => {
                  const isActive = status?.active_user?.email === u.email;
                  return (
                    <div key={u.id} className={`user-row ${isActive ? "user-row-active" : ""}`}>
                      <div className="user-info">
                        <div className="user-name-line">
                          <span className="user-name">{u.fullname}</span>
                          {u.has_ai_profile && <span className="badge accent">Perfil IA</span>}
                        </div>
                        <div className="user-sub">
                          {u.email} · Sesiones activas: {u.active_sessions}
                        </div>
                      </div>
                      <button
                        className={`btn ${isActive ? "btn-success-outline" : "btn-primary"}`}
                        type="button"
                        disabled={isActive}
                        onClick={() => void switchUser(u)}
                      >
                        {isActive ? "✓ Activo" : "Cambiar"}
                      </button>
                    </div>
                  );
                })}
                {users.length === 0 && <p className="card-desc">No se detectaron usuarios en public.profile.</p>}
              </div>
            </div>

            <div className="card">
              <div className="card-head">
                <h2 className="card-title">👁️ Escáner Semántico & Espacial</h2>
                <span className="badge ok">pgvector + Valkey</span>
              </div>

              <search>
                <form className="vision-search-row" onSubmit={handleVisionSubmit}>
                  <label htmlFor={`${baseId}-vision-q`} className="visually-hidden">
                    Consulta semántica en el lienzo
                  </label>
                  <input
                    id={`${baseId}-vision-q`}
                    name="q"
                    type="search"
                    className="input"
                    value={visionQuery}
                    onChange={(e) => setVisionQuery(e.target.value)}
                    placeholder="Buscar elementos en el lienzo (ej. Hero Card, Navbar)..."
                  />
                  <button className="btn btn-primary btn-nowrap" type="submit" disabled={visionLoading}>
                    {visionLoading ? "Escaneando..." : "🔍 Escanear"}
                  </button>
                </form>
              </search>

              <div className="kpi-grid">
                <div className="kpi-box">
                  <span className="kpi-label">Motor Vectorial</span>
                  <strong className="kpi-val">{visionResult?.engine || "pgvector"}</strong>
                </div>
                <div className="kpi-box">
                  <span className="kpi-label">Shapes Indexados</span>
                  <strong className="kpi-val">
                    {visionResult?.total_shapes ?? status?.pgvector?.indexed_shapes_count ?? 0}
                  </strong>
                </div>
                <div className="kpi-box">
                  <span className="kpi-label">Origen Libre Sugerido</span>
                  <strong className="kpi-val">
                    {visionResult?.recommended_free_origin
                      ? `(${visionResult.recommended_free_origin.x}, ${visionResult.recommended_free_origin.y})`
                      : "N/A"}
                  </strong>
                </div>
              </div>

              <div className="code-box">{visionResult?.prompt_summary || "Sin resultados para esta consulta."}</div>
            </div>
          </div>
        </section>

        {/* TAB 4: ARQUITECTURA */}
        <section
          role="tabpanel"
          id={`${baseId}-panel-inst`}
          aria-labelledby={`${baseId}-tab-inst`}
          hidden={activeTab !== "inst"}
          className="panel-scroll"
        >
          <div className="panel-inner">
            <div className="card">
              <div className="card-head">
                <h2 className="card-title">Stack Verificado v0.7.0</h2>
                <span className="badge ok">En Línea</span>
              </div>
              <div className="arch-grid">
                <div className="arch-tile">
                  <div className="arch-tile-head">
                    <strong>Frontend SPA</strong>
                    <span className="badge accent">Nginx :8787</span>
                  </div>
                  <p>React 19 + TypeScript + Zod. Validación estricta de contratos en runtime y rutas unificadas.</p>
                </div>
                <div className="arch-tile">
                  <div className="arch-tile-head">
                    <strong>WebSockets Hub</strong>
                    <span className={`badge ${wsConnected ? "ok" : "warn"}`}>
                      {wsConnected ? "Go 1.24 Live" : "Offline"}
                    </span>
                  </div>
                  <p>
                    Servicio dedicado en Go (<code>penpot-ws-go:4402</code>) gestionando <code>/plugin/ws</code> con
                    baja latencia.
                  </p>
                </div>
                <div className="arch-tile">
                  <div className="arch-tile-head">
                    <strong>Backend MCP</strong>
                    <span className="badge ok">Python 3.14</span>
                  </div>
                  <p>FastMCP + Pydantic v2. 14 sub-herramientas comprimidas con trazabilidad y perfiles por usuario.</p>
                </div>
                <div className="arch-tile">
                  <div className="arch-tile-head">
                    <strong>Caché Valkey 8.1</strong>
                    <span className="badge">{valkeyHitRate}% Hit Rate</span>
                  </div>
                  <p>
                    Hits: {valkeyHits} · Misses: {valkeyMisses} · TTL: {status?.valkey_cache?.default_ttl ?? 120}s
                    (invalidación O(1) por epoch).
                  </p>
                </div>
                <div className="arch-tile arch-tile-wide">
                  <div className="arch-tile-head">
                    <strong>Base de Datos Vectorial</strong>
                    <span className="badge ok">
                      PostgreSQL 17.12 + pgvector {status?.pgvector?.pgvector_version || "0.8.7"}
                    </span>
                  </div>
                  <p>
                    Extensión <code>pg_trgm</code> {status?.pgvector?.pg_trgm_version || "1.6"} · Shapes indexados:{" "}
                    {status?.pgvector?.indexed_shapes_count ?? 0} · Usuarios con perfil IA:{" "}
                    {status?.pgvector?.users_with_ai_profiles ?? 0}.
                  </p>
                </div>
              </div>
            </div>
          </div>
        </section>
      </main>
    </div>
  );
}
