import { API_BASE_URL } from './admin';

const configuredVersionCheckUrl = String(import.meta.env.VITE_VERSION_CHECK_URL || '').trim();
const defaultVersionCheckUrl = `${API_BASE_URL}/api/updates/remote-version`;

export interface RemoteVersionDebugEvent {
  stage: string;
  url: string;
  status?: number;
  contentType?: string;
  parsedVersion?: string | null;
  source?: string | null;
  errors?: string[];
  note?: string;
}

const normalizeVersion = (rawVersion: string): string => rawVersion.trim().replace(/^v(?=\d)/i, '');

const isLikelyVersion = (rawValue: string): boolean => (
  rawValue.length > 0
  && rawValue.length <= 64
  && !/[<>\s]/.test(rawValue)
  && /^[\w.+-]+$/.test(rawValue)
);

const pickVersionFromPayload = (payload: unknown): string | null => {
  if (typeof payload === 'string') {
    const normalized = normalizeVersion(payload);
    return isLikelyVersion(normalized) ? normalized : null;
  }

  if (!payload || typeof payload !== 'object') {
    return null;
  }

  const payloadRecord = payload as Record<string, unknown>;
  const candidateKeys = ['version', 'latest_version', 'tag_name', 'app_version'] as const;
  for (const key of candidateKeys) {
    const candidate = payloadRecord[key];
    if (typeof candidate === 'string' && candidate.trim()) {
      const normalized = normalizeVersion(candidate);
      if (isLikelyVersion(normalized)) {
        return normalized;
      }
    }
  }

  const nestedPayloadKeys = ['data', 'result', 'payload'] as const;
  for (const key of nestedPayloadKeys) {
    const nestedPayload = payloadRecord[key];
    if (nestedPayload && typeof nestedPayload === 'object') {
      const nestedVersion = pickVersionFromPayload(nestedPayload);
      if (nestedVersion) {
        return nestedVersion;
      }
    }
  }

  return null;
};

const parsePossiblyJsonText = (text: string): unknown => {
  const trimmed = text.trim();
  if (!trimmed) {
    return text;
  }

  const likelyJson = (trimmed.startsWith('{') && trimmed.endsWith('}'))
    || (trimmed.startsWith('[') && trimmed.endsWith(']'));
  if (!likelyJson) {
    return text;
  }

  try {
    return JSON.parse(trimmed);
  } catch {
    return text;
  }
};

export const getRemoteVersion = async (
  onDebugEvent?: (event: RemoteVersionDebugEvent) => void,
): Promise<string | null> => {
  const url = configuredVersionCheckUrl || defaultVersionCheckUrl;
  onDebugEvent?.({
    stage: 'request_start',
    url,
    note: configuredVersionCheckUrl ? 'using VITE_VERSION_CHECK_URL' : 'using backend proxy endpoint',
  });

  const response = await fetch(url, { method: 'GET' });
  if (!response.ok) {
    onDebugEvent?.({
      stage: 'request_failed',
      url,
      status: response.status,
      note: 'non-2xx response',
    });
    throw new Error(`Version check failed, status code: ${response.status}`);
  }

  const contentType = (response.headers.get('content-type') || '').toLowerCase();
  let payload: unknown;
  if (contentType.includes('application/json') || contentType.includes('+json')) {
    payload = await response.json();
  } else {
    const text = await response.text();
    payload = parsePossiblyJsonText(text);
  }

  const parsedVersion = pickVersionFromPayload(payload);
  const payloadRecord = payload && typeof payload === 'object' ? payload as Record<string, unknown> : null;
  const source = typeof payloadRecord?.source === 'string' ? payloadRecord.source : null;
  const errors = Array.isArray(payloadRecord?.errors)
    ? payloadRecord?.errors.filter((item): item is string => typeof item === 'string')
    : undefined;

  onDebugEvent?.({
    stage: 'response_parsed',
    url,
    status: response.status,
    contentType,
    parsedVersion,
    source,
    errors,
    note: parsedVersion ? 'version parsed successfully' : 'version not found from payload',
  });

  return parsedVersion;
};

export const normalizeComparableVersion = (rawVersion: string): string =>
  normalizeVersion(rawVersion);
