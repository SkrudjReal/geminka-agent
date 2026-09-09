/** IDs verified against LanguageServer.GetCascadeModelConfig, 2026-09-07. */
export interface ModelMapping {
  id: string;
  internalId: string;
  displayName: string;
  provider: string;
}

const FAMILIES: Record<string, [string, string, string]> = {
  'gemini-3.8-flash': ['MODEL_PLACEHOLDER_M320', 'MODEL_PLACEHOLDER_M319', 'MODEL_PLACEHOLDER_M318'],
  'gemini-3.7-flash': ['MODEL_PLACEHOLDER_M300', 'MODEL_PLACEHOLDER_M299', 'MODEL_PLACEHOLDER_M298'],
  'gemini-3.6-flash': ['MODEL_PLACEHOLDER_M73', 'MODEL_PLACEHOLDER_M72', 'MODEL_PLACEHOLDER_M71'],
};
const FIXED: Record<string, string> = {
  'claude-sonnet-4-6': 'MODEL_PLACEHOLDER_M35',
  'claude-opus-4-6': 'MODEL_PLACEHOLDER_M26',
};

export function resolveModelId(externalName?: string, reasoningEffort?: string): string {
  const name = (externalName || 'gemini-3.7-flash').toLowerCase().trim().replace(/^google-antigravity\//, '');
  if (FIXED[name]) return FIXED[name];
  const suffix = name.match(/-(low|medium|high)$/);
  const family = suffix ? name.slice(0, -suffix[0].length) : name;
  if (!FAMILIES[family]) throw new Error(`Unsupported model: ${externalName}`);
  const effort = (reasoningEffort || suffix?.[1] || 'high').toLowerCase().trim();
  const index = ['low', 'medium', 'high'].indexOf(effort === 'minimal' ? 'low' : effort);
  if (index < 0) throw new Error(`Unsupported reasoning effort: ${reasoningEffort}`);
  return FAMILIES[family][index];
}

export function getAllModels(): ModelMapping[] {
  return [...Object.keys(FAMILIES), ...Object.keys(FIXED)].map(name => ({
    id: `google-antigravity/${name}`,
    internalId: resolveModelId(name),
    displayName: name,
    provider: name.startsWith('claude') ? 'anthropic' : 'google',
  }));
}

export function toOpenAIModelsResponse() {
  return { object: 'list', data: getAllModels().map(model => ({
    id: model.id, object: 'model', created: Math.floor(Date.now() / 1000), owned_by: model.provider,
  })) };
}
