import type { AgentChatResponse, UploadedAttachment } from "./schemas";

export interface ChatMessage {
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

export type StudioTab = "chat" | "routing" | "keys" | "users" | "inst";

export const TABS: { id: StudioTab; label: string; shortLabel: string }[] = [
  { id: "chat", label: "💬 Studio Chat", shortLabel: "Chat" },
  { id: "routing", label: "🤖 Agente / Perfil", shortLabel: "Agente" },
  { id: "keys", label: "🔑 API Keys", shortLabel: "Keys" },
  { id: "users", label: "👥 Usuarios & pgvector", shortLabel: "Usuarios" },
  { id: "inst", label: "🏗️ Arquitectura", shortLabel: "Stack" },
];

export function nowLabel(): string {
  return new Date().toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit" });
}
