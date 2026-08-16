"use client";

// AdminSettingsModal.tsx — runtime LLM provider config (save-and-replace, no confirm)
//
// Triggered from the top-bar ⚙ button.  Lets the user pick the active
// provider + model + base URL + API key, then POSTs the change to
// `/api/admin/config`.  Per the product spec, this is direct-save:
// there is NO confirm step.  The backend writes the new values to
// `.env`, clears the settings LRU cache, and the new config is live
// for the very next request — no service restart required.

import { useEffect, useState } from "react";
import {
  ConfigSnapshot,
  fetchConfig,
  postConfig,
} from "@/lib/adminApi";

const PROVIDER_OPTIONS: { value: string; label: string; defaultModel: string; defaultBaseUrl: string }[] = [
  { value: "minimax", label: "MiniMax (M2/M3, OpenAI-compatible)", defaultModel: "MiniMax-M3", defaultBaseUrl: "https://api.minimaxi.com/v1" },
  { value: "agnes", label: "Agnes AI (OpenAI-compatible)", defaultModel: "agnes-2.0-flash", defaultBaseUrl: "https://apihub.agnes-ai.com/v1" },
  { value: "openai", label: "OpenAI", defaultModel: "gpt-4o-mini", defaultBaseUrl: "https://api.openai.com/v1" },
  { value: "deepseek", label: "DeepSeek (OpenAI-compatible)", defaultModel: "deepseek-chat", defaultBaseUrl: "https://api.deepseek.com/v1" },
  { value: "anthropic", label: "Anthropic Claude", defaultModel: "claude-3-5-sonnet-20241022", defaultBaseUrl: "" },
  { value: "ollama", label: "Ollama (local)", defaultModel: "llama3.1:8b", defaultBaseUrl: "http://localhost:11434" },
  { value: "mock", label: "Mock (offline, no key required)", defaultModel: "", defaultBaseUrl: "" },
];

interface AdminSettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSaved?: (snapshot: ConfigSnapshot) => void;
}

export default function AdminSettingsModal({
  isOpen,
  onClose,
  onSaved,
}: AdminSettingsModalProps) {
  const [snapshot, setSnapshot] = useState<ConfigSnapshot | null>(null);
  const [provider, setProvider] = useState<string>("");
  const [model, setModel] = useState<string>("");
  const [baseUrl, setBaseUrl] = useState<string>("");
  const [apiKey, setApiKey] = useState<string>(""); // never pre-fill; only set on edit
  const [loading, setLoading] = useState<boolean>(false);
  const [saving, setSaving] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<string | null>(null);

  // Load current config when the modal opens
  useEffect(() => {
    if (!isOpen) return;
    setError(null);
    setSavedAt(null);
    setLoading(true);
    fetchConfig()
      .then((cfg) => {
        setSnapshot(cfg);
        setProvider(cfg.provider);
        setModel(cfg.model);
        setBaseUrl(cfg.base_url);
        setApiKey(""); // never echo the redacted value back into the form
      })
      .catch((e) => setError(String(e?.message || e)))
      .finally(() => setLoading(false));
  }, [isOpen]);

  // When the user picks a different provider, pre-fill model + base_url
  // with sensible defaults if the current values look like they came
  // from a different provider.  (Heuristic: only override when the
  // current model doesn't match the new provider's default.)
  useEffect(() => {
    const opt = PROVIDER_OPTIONS.find((o) => o.value === provider);
    if (!opt) return;
    if (model.trim() === "" || isModelFromOtherProvider(model, provider)) {
      setModel(opt.defaultModel);
    }
    if (baseUrl.trim() === "" || isBaseUrlFromOtherProvider(baseUrl, provider)) {
      setBaseUrl(opt.defaultBaseUrl);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider]);

  if (!isOpen) return null;

  const handleProviderChange = (next: string) => {
    setProvider(next);
  };

  const handleSave = async () => {
    setError(null);
    setSavedAt(null);
    setSaving(true);
    try {
      const payload: {
        provider?: string;
        model?: string;
        base_url?: string;
        api_key?: string;
      } = {};
      // Always send provider so the change is explicit
      if (provider) payload.provider = provider;
      // Only send model / base_url if user actually edited them
      if (model !== (snapshot?.model ?? "")) payload.model = model;
      if (baseUrl !== (snapshot?.base_url ?? "")) payload.base_url = baseUrl;
      // Only send api_key if the field has content (sending "" clears)
      if (apiKey.length > 0) payload.api_key = apiKey;
      // Send the snapshot to compare against post-save
      const before = snapshot;
      const after = await postConfig(payload);
      setSnapshot(after);
      setApiKey(""); // wipe the input after save
      setSavedAt(new Date().toLocaleTimeString());
      onSaved?.(after);
      // Surface a useful message when the active provider actually changed
      if (before && before.provider !== after.provider) {
        // no-op; the GET re-render below reflects the new state
      }
    } catch (e) {
      setError(String((e as Error)?.message || e));
    } finally {
      setSaving(false);
    }
  };

  const handleClearKey = async () => {
    if (!confirm("Clear the active provider's API key? You'll be switched to the next available provider.")) return;
    setError(null);
    setSavedAt(null);
    setSaving(true);
    try {
      const after = await postConfig({ api_key: "" });
      setSnapshot(after);
      setSavedAt(new Date().toLocaleTimeString());
      onSaved?.(after);
    } catch (e) {
      setError(String((e as Error)?.message || e));
    } finally {
      setSaving(false);
    }
  };

  const handleClose = () => {
    if (saving) return;
    onClose();
  };

  const hasKey = !!snapshot?.api_key_redacted;
  const showKeyField = provider !== "mock" && provider !== "ollama";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={handleClose}
      data-testid="admin-settings-backdrop"
    >
      <div
        className="w-[520px] max-w-[92vw] rounded-xl border border-[#C7A969]/30 bg-[#1F1F1F] p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
        data-testid="admin-settings-modal"
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-[#C7A969]">⚙ 模型配置</h2>
          <button
            type="button"
            onClick={handleClose}
            className="text-[#E8E1D4]/60 hover:text-[#E8E1D4]"
            data-testid="admin-settings-close"
            aria-label="关闭"
          >
            ✕
          </button>
        </div>

        {loading ? (
          <div className="py-12 text-center text-[#E8E1D4]/70">加载中…</div>
        ) : (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              handleSave();
            }}
            className="space-y-4"
          >
            <FieldRow label="Provider">
              <select
                value={provider}
                onChange={(e) => handleProviderChange(e.target.value)}
                className="w-full rounded-md border border-[#C7A969]/30 bg-[#1A1814] px-3 py-2 text-sm text-[#E8E1D4] focus:border-[#C7A969] focus:outline-none"
                data-testid="admin-settings-provider"
              >
                {PROVIDER_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </FieldRow>

            <FieldRow label="Model">
              <input
                type="text"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder="例如 MiniMax-M3 / gpt-4o-mini"
                className="w-full rounded-md border border-[#C7A969]/30 bg-[#1A1814] px-3 py-2 text-sm text-[#E8E1D4] focus:border-[#C7A969] focus:outline-none"
                data-testid="admin-settings-model"
              />
            </FieldRow>

            <FieldRow label="Base URL">
              <input
                type="text"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder="https://api.example.com/v1"
                className="w-full rounded-md border border-[#C7A969]/30 bg-[#1A1814] px-3 py-2 text-sm text-[#E8E1D4] focus:border-[#C7A969] focus:outline-none"
                data-testid="admin-settings-base-url"
              />
            </FieldRow>

            {showKeyField && (
              <FieldRow
                label={
                  <span>
                    API Key
                    {hasKey && (
                      <span className="ml-2 text-xs text-[#5C7367]">
                        当前: {snapshot?.api_key_redacted}
                      </span>
                    )}
                  </span>
                }
              >
                <div className="flex gap-2">
                  <input
                    type="password"
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    placeholder={hasKey ? "留空表示不修改" : "输入新 key"}
                    className="flex-1 rounded-md border border-[#C7A969]/30 bg-[#1A1814] px-3 py-2 text-sm text-[#E8E1D4] focus:border-[#C7A969] focus:outline-none"
                    data-testid="admin-settings-api-key"
                  />
                  {hasKey && (
                    <button
                      type="button"
                      onClick={handleClearKey}
                      disabled={saving}
                      className="rounded-md border border-[#8B3A3A]/40 px-3 py-2 text-xs text-[#8B3A3A] hover:bg-[#8B3A3A]/10 disabled:opacity-50"
                      data-testid="admin-settings-clear-key"
                    >
                      清除
                    </button>
                  )}
                </div>
              </FieldRow>
            )}

            {error && (
              <div
                className="rounded-md border border-[#8B3A3A]/40 bg-[#8B3A3A]/10 px-3 py-2 text-xs text-[#E8E1D4]"
                data-testid="admin-settings-error"
              >
                {error}
              </div>
            )}
            {savedAt && !error && (
              <div
                className="rounded-md border border-[#5C7367]/40 bg-[#5C7367]/10 px-3 py-2 text-xs text-[#E8E1D4]"
                data-testid="admin-settings-saved"
              >
                ✓ 已保存于 {savedAt} — 新配置已生效
              </div>
            )}

            <div className="flex items-center justify-between border-t border-[#C7A969]/20 pt-3 text-xs text-[#E8E1D4]/50">
              <span>写入: {snapshot?.env_path || "…"}</span>
              <span>改动直接落盘 · 无需重启</span>
            </div>

            <div className="flex justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={handleClose}
                disabled={saving}
                className="rounded-md border border-[#C7A969]/30 px-4 py-2 text-sm text-[#E8E1D4] hover:bg-[#1A1814] disabled:opacity-50"
                data-testid="admin-settings-cancel"
              >
                取消
              </button>
              <button
                type="submit"
                disabled={saving}
                className="rounded-md border border-[#C7A969] bg-[#C7A969]/20 px-4 py-2 text-sm text-[#C7A969] hover:bg-[#C7A969]/30 disabled:opacity-50"
                data-testid="admin-settings-save"
              >
                {saving ? "保存中…" : "保存"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}

function FieldRow({
  label,
  children,
}: {
  label: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="mb-1 block text-xs uppercase tracking-wide text-[#E8E1D4]/60">
        {label}
      </label>
      {children}
    </div>
  );
}

function isModelFromOtherProvider(model: string, _target: string): boolean {
  // Heuristic: if the model string doesn't include a known provider
  // prefix, leave it alone (user likely typed something custom).
  // Otherwise treat it as belonging to the previous provider.
  if (!model) return false;
  if (model.toLowerCase().includes("minimax")) return true;
  if (model.toLowerCase().includes("agnes")) return true;
  if (model.toLowerCase().includes("gpt-")) return true;
  if (model.toLowerCase().includes("claude")) return true;
  if (model.toLowerCase().includes("deepseek")) return true;
  if (model.toLowerCase().includes("llama")) return true;
  return false;
}

function isBaseUrlFromOtherProvider(url: string, _target: string): boolean {
  if (!url) return false;
  if (url.includes("minimaxi.com")) return true;
  if (url.includes("agnes-ai.com")) return true;
  if (url.includes("api.openai.com")) return true;
  if (url.includes("api.deepseek.com")) return true;
  if (url.includes("localhost:11434")) return true;
  return false;
}
