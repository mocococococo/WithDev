import type { User } from 'firebase/auth';

import { fetchWithAuth, readErrorDetail } from './http';

const defaultMinutesError = '議事録の生成に失敗しました。時間をおいて再試行してください。';

type MinutesFromTextResponse = {
  minutes?: {
    body?: unknown;
  };
};

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
  let response: Response;
  try {
    response = await fetchWithAuth(user, '/api/minutes/from-text', {
      method: 'POST',
      body: JSON.stringify({ text }),
    });
  } catch (err) {
    throw err instanceof Error ? err : new Error('バックエンドに接続できません。');
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
