import type { AgentStatus } from "../schemas";

interface ArchitectureTabProps {
  baseId: string;
  hidden: boolean;
  status: AgentStatus | null;
  wsConnected: boolean;
  valkeyHitRate: number;
  valkeyHits: number;
  valkeyMisses: number;
}

export function ArchitectureTab({
  baseId,
  hidden,
  status,
  wsConnected,
  valkeyHitRate,
  valkeyHits,
  valkeyMisses,
}: ArchitectureTabProps) {
  const agentDb = status?.agent_db;

  return (
    <section
      role="tabpanel"
      id={`${baseId}-panel-inst`}
      aria-labelledby={`${baseId}-tab-inst`}
      hidden={hidden}
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
                <span className="badge accent">Nginx Gateway</span>
              </div>
              <p>React 19 + TypeScript + Zod. Contratos SDD consumidos desde /agent/status sin catálogos hardcoded.</p>
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
                <strong>Backend MCP &amp; Dual-DB</strong>
                <span className="badge ok">Python 3.14</span>
              </div>
              <p>
                FastMCP + Pydantic v2. Capa agéntica: <code>{agentDb?.backend || "penpot_postgres"}</code> (
                {agentDb?.isolated ? "aislada" : "compartida"} · target: <code>{agentDb?.target || "penpot-postgres"}</code>).
              </p>
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
  );
}
