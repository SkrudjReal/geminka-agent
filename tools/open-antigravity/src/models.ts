/**
 * Model name and reasoning effort mapping to Antigravity internal IDs.
 *
 * Gemini 3.7 Flash enums:
 * - High Thinking:   MODEL_PLACEHOLDER_M298
 * - Medium Thinking: MODEL_PLACEHOLDER_M299
 * - Low Thinking:    MODEL_PLACEHOLDER_M300
 */

export interface ModelMapping {
  id: string;
  internalId: string;
  displayName: string;
  provider: string;
}

const MODEL_MAP: ModelMapping[] = [
  {
    id: 'google-antigravity/gemini-3.7-flash',
    internalId: 'MODEL_PLACEHOLDER_M298',
    displayName: 'Gemini 3.7 Flash (High)',
    provider: 'google',
  },
  {
    id: 'google-antigravity/gemini-3.7-flash-high',
    internalId: 'MODEL_PLACEHOLDER_M298',
    displayName: 'Gemini 3.7 Flash (High)',
    provider: 'google',
  },
  {
    id: 'google-antigravity/gemini-3.7-flash-medium',
    internalId: 'MODEL_PLACEHOLDER_M299',
    displayName: 'Gemini 3.7 Flash (Medium)',
    provider: 'google',
  },
  {
    id: 'google-antigravity/gemini-3.7-flash-low',
    internalId: 'MODEL_PLACEHOLDER_M300',
    displayName: 'Gemini 3.7 Flash (Low)',
    provider: 'google',
  },
  {
    id: 'gemini-3.7-flash',
    internalId: 'MODEL_PLACEHOLDER_M298',
    displayName: 'Gemini 3.7 Flash',
    provider: 'google',
  },
];

/**
 * Resolves model name and reasoning effort level to the exact Antigravity enum.
 */
export function resolveModelId(externalName?: string, reasoningEffort?: string): string {
  const effort = (reasoningEffort || '').toLowerCase().trim();

  if (effort === 'low' || effort === 'minimal') {
    return 'MODEL_PLACEHOLDER_M300'; // Gemini 3.7 Flash Low Thinking
  }
  if (effort === 'medium') {
    return 'MODEL_PLACEHOLDER_M299'; // Gemini 3.7 Flash Medium Thinking
  }
  if (effort === 'high') {
    return 'MODEL_PLACEHOLDER_M298'; // Gemini 3.7 Flash High Thinking
  }

  const name = (externalName || '').toLowerCase().trim();
  if (name.includes('low')) return 'MODEL_PLACEHOLDER_M300';
  if (name.includes('medium')) return 'MODEL_PLACEHOLDER_M299';
  if (name.includes('high')) return 'MODEL_PLACEHOLDER_M298';

  return 'MODEL_PLACEHOLDER_M298';
}

export function getAllModels(): ModelMapping[] {
  return MODEL_MAP;
}

export function toOpenAIModelsResponse() {
  return {
    object: 'list',
    data: [
      {
        id: 'google-antigravity/gemini-3.7-flash',
        object: 'model',
        created: Math.floor(Date.now() / 1000),
        owned_by: 'google',
      },
    ],
  };
}
