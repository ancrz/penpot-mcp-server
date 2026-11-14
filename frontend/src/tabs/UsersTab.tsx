import type { FormEvent } from "react";
import type { AgentStatus, PenpotUserRow, PgvectorVisionResponse } from "../schemas";

interface UsersTabProps {
  baseId: string;
  hidden: boolean;
  status: AgentStatus | null;
  users: PenpotUserRow[];
  visionQuery: string;
  visionResult: PgvectorVisionResponse | null;
  visionLoading: boolean;
  onVisionQueryChange: (val: string) => void;
  onVisionSubmit: (e: FormEvent) => void;
  onSwitchUser: (u: PenpotUserRow) => Promise<void>;
}

export function UsersTab({
  baseId,
  hidden,
  status,
  users,
  visionQuery,
  visionResult,
  visionLoading,
  onVisionQueryChange,
  onVisionSubmit,
  onSwitchUser,
}: UsersTabProps) {
  return (
    <section
      role="tabpanel"
      id={`${baseId}-panel-users`}
      aria-labelledby={`${baseId}-tab-users`}
      hidden={hidden}
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
                    onClick={() => void onSwitchUser(u)}
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
            <h2 className="card-title">👁️ Escáner Semántico &amp; Espacial</h2>
            <span className="badge ok">pgvector + Valkey</span>
          </div>

          <search>
            <form className="vision-search-row" onSubmit={onVisionSubmit}>
              <label htmlFor={`${baseId}-vision-q`} className="visually-hidden">
                Consulta semántica en el lienzo
              </label>
              <input
                id={`${baseId}-vision-q`}
                name="q"
                type="search"
                className="input"
                value={visionQuery}
                onChange={(e) => onVisionQueryChange(e.target.value)}
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
  );
}
