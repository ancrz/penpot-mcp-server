import type { FormEvent } from "react";
import type { AgentStatus, ProviderCatalogEntry } from "../schemas";

interface ProfileTabProps {
  baseId: string;
  hidden: boolean;
  status: AgentStatus | null;
  providerCatalog: Record<string, ProviderCatalogEntry>;
  provider: string;
  model: string;
  onProviderChange: (nextProvider: string) => void;
  onModelChange: (nextModel: string) => void;
  onSaveProfile: (e: FormEvent) => Promise<void>;
}

export function ProfileTab({
  baseId,
  hidden,
  status,
  providerCatalog,
  provider,
  model,
  onProviderChange,
  onModelChange,
  onSaveProfile,
}: ProfileTabProps) {
  return (
    <section
      role="tabpanel"
      id={`${baseId}-panel-routing`}
      aria-labelledby={`${baseId}-tab-routing`}
      hidden={hidden}
      className="panel-scroll"
    >
      <div className="panel-inner">
        <form className="card" onSubmit={(e) => void onSaveProfile(e)} aria-labelledby={`${baseId}-routing-title`}>
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
              onChange={(e) => onProviderChange(e.target.value)}
            >
              {Object.entries(providerCatalog).map(([key, info]) => (
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
              onChange={(e) => onModelChange(e.target.value)}
              placeholder="Ej. gemini-3.5-flash-lite"
              autoCorrect="off"
              autoCapitalize="none"
              spellCheck={false}
              required
            />
            <div className="preset-chips" aria-label="Modelos recomendados">
              {(providerCatalog[provider]?.models ?? []).map((presetModel) => (
                <button
                  key={presetModel}
                  type="button"
                  className={`chip ${model === presetModel ? "chip-selected" : ""}`}
                  onClick={() => onModelChange(presetModel)}
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
  );
}
