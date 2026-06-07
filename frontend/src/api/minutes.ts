import type { User } from 'firebase/auth';

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? '';
const defaultMinutesError = '議事録の生成に失敗しました。時間をおいて再試行してください。';

type MinutesFromTextResponse = {
  minutes?: {
    body?: unknown;
  };
};

function buildApiUrl(path: string) {
  return `${apiBaseUrl}${path}`;
}

async function readErrorDetail(response: Response) {
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

function getReadableMinutesError(detail: string, status: number) {
  if (detail === 'text is required') {
    return '文字起こしを入力してください。';
  }

  if (detail === 'text must be 50000 characters or less') {
    return '文字起こしは50,000文字以内にしてください。';
  }

  if (
    status === 401 ||
    detail === 'authorization bearer token is required' ||
    detail === 'invalid firebase id token'
  ) {
    return 'ログイン状態を確認してください。';
  }

  if (detail === 'failed to generate minutes') {
    return defaultMinutesError;
  }

  return defaultMinutesError;
}

export async function generateMinutesFromText(user: User, text: string) {
  let token: string;
  try {
    token = await user.getIdToken();
  } catch {
    throw new Error('ログイン状態を確認してください。');
  }

  let response: Response;
  try {
    response = await fetch(buildApiUrl('/api/minutes/from-text'), {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ text }),
    });
  } catch {
    throw new Error('バックエンドに接続できません。');
  }

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(getReadableMinutesError(detail, response.status));
  }

  const payload = (await response.json()) as MinutesFromTextResponse;
  const body = payload.minutes?.body;
  if (typeof body !== 'string') {
    throw new Error('議事録生成APIのレスポンスを読み取れませんでした。');
  }

  return body;
}
