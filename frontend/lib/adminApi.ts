// lib/adminApi.ts — admin configuration endpoints
//
// Front-end wrapper for the GET/POST /api/admin/config endpoints that
// let the user change the active LLM provider/model/api_key at runtime
// without restarting the backend.  Per the product spec ("默认直接保存
// 更新替换到后端"), saving here is immediate and irreversible — there
// is no confirm step.

const baseHttp = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export type ProviderName =
  | "mock"
  | "minimax"
  | "agnes"
  | "openai"
  | "deepseek"
  | "anthropic"
  | "ollama";

export interface ConfigSnapshot {
  provider: string;
  model: string;
  base_url: string;
  api_key_redacted: string | null;
  use_mock_llm: boolean;
  use_letta: boolean;
  env_path: string;
}

export interface ConfigUpdate {
  provider?: ProviderName | string;
  model?: string;
  base_url?: string;
  api_key?: string; // pass "" to clear
}

export async function fetchConfig(): Promise<ConfigSnapshot> {
  const r = await fetch(`${baseHttp}/api/admin/config`, {
    method: "GET",
    cache: "no-store",
  });
  if (!r.ok) {
    const detail = await r.text().catch(() => r.statusText);
    throw new Error(`fetchConfig failed: ${r.status} ${detail}`);
  }
  return r.json();
}

export async function postConfig(update: ConfigUpdate): Promise<ConfigSnapshot> {
  const r = await fetch(`${baseHttp}/api/admin/config`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(update),
  });
  if (!r.ok) {
    const detail = await r.text().catch(() => r.statusText);
    throw new Error(`postConfig failed: ${r.status} ${detail}`);
  }
  return r.json();
}
