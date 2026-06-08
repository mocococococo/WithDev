import type { User } from 'firebase/auth';

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? '';

export function buildApiUrl(path: string) {
  return `${apiBaseUrl}${path}`;
}

export async function readErrorDetail(response: Response) {
  try {
    const payload: unknown = await response.json();
    if (payload && typeof payload === 'object' && 'detail' in payload) {
      const detail = (payload as { detail?: unknown }).detail;
      return typeof detail === 'string' ? detail : response.statusText;
    }
  } catch {
    // Fall back to the status text below.
  }

  return response.statusText;
}

export async function fetchWithAuth(
  user: User,
  path: string,
  init: RequestInit = {},
) {
  let token: string;
  try {
    token = await user.getIdToken();
  } catch {
    throw new Error('ログイン状態を確認してください。');
  }

  const headers = new Headers(init.headers);
  headers.set('Authorization', `Bearer ${token}`);

  if (init.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  try {
    return await fetch(buildApiUrl(path), {
      ...init,
      headers,
    });
  } catch {
    throw new Error('バックエンドに接続できません。');
  }
}
