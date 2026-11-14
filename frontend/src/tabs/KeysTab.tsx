import type { FormEvent, RefObject } from "react";
import type { AgentStatus, ProviderCatalogEntry } from "../schemas";

interface KeysTabProps {
  baseId: string;
  hidden: boolean;
  status: AgentStatus | null;
  providerCatalog: Record<string, ProviderCatalogEntry>;
  provider: string;
  apiKeyName: string;
  apiKeyVal: string;
  showKey: boolean;
  keyError: string;
  keyInputRef: RefObject<HTMLInputElement | null>;
  onProviderChange: (nextProvider: string) => void;
  onApiKeyNameChange: (val: string) => void;
  onApiKeyValChange: (val: string) => void;
  onToggleShowKey: () => void;
  onSaveKey: (e: FormEvent) => Promise<void>;
}

export function KeysTab({
  baseId,
  hidden,
  status,
  providerCatalog,
  provider,
  apiKeyName,
  apiKeyVal,
  showKey,
  keyError,
  keyInputRef,
  onProviderChange,
  onApiKeyNameChange,
  onApiKeyValChange,
  onToggleShowKey,
  onSaveKey,
}: KeysTabProps) {
  return (
    <section
      role="tabpanel"
      id={`${baseId}-panel-keys`}
      aria-labelledby={`${baseId}-tab-keys`}
      hidden={hidden}
      className="panel-scroll"
    >
      <div className="panel-inner">
        <form className="card" onSubmit={(e) => void onSaveKey(e)} noValidate aria-labelledby={`${baseId}-keys-title`}>
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
              <label htmlFor={`${baseId}-key-name`}>Nombre de perfil</label>
              <input
                id={`${baseId}-key-name`}
                name="profile_name"
                className="input"
                value={apiKeyName}
                onChange={(e) => onApiKeyNameChange(e.target.value)}
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
                onChange={(e) => onApiKeyValChange(e.target.value)}
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
                onClick={onToggleShowKey}
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
  );
}
