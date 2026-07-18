import type { User } from 'firebase/auth';
import { fetchWithAuth, readErrorDetail } from './http';

export type NotionConnectionStatus = {
  connected: boolean;
  notion_workspace_id: string | null;
  notion_workspace_name: string | null;
  default_database_id: string | null;
  default_database_name: string | null;
};

export type NotionDatabase = {
  id: string;
  title: string;
};

type NotionOAuthStartResponse = {
  url?: string;
};

type NotionConnectionResponse = {
  connection?: NotionConnectionStatus;
};

type NotionDatabasesResponse = {
  databases?: NotionDatabase[];
};

export async function startNotionOAuth(user: User, teamId: string): Promise<string> {
  const response = await fetchWithAuth(user, `/api/teams/${teamId}/notion/oauth/start`);
  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(toNotionError(detail, response.status));
  }

  const payload = (await response.json()) as NotionOAuthStartResponse;
  if (typeof payload.url !== 'string') {
    throw new Error('Notion連携URLを取得できませんでした。');
  }
  return payload.url;
}

export async function fetchNotionConnection(
  user: User,
  teamId: string,
): Promise<NotionConnectionStatus> {
  const response = await fetchWithAuth(user, `/api/teams/${teamId}/notion/connection`);
  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(toNotionError(detail, response.status));
  }

  const payload = (await response.json()) as NotionConnectionResponse;
  if (!payload.connection) {
    throw new Error('Notion連携状態を取得できませんでした。');
  }
  return payload.connection;
}

export async function fetchNotionDatabases(user: User, teamId: string): Promise<NotionDatabase[]> {
  const response = await fetchWithAuth(user, `/api/teams/${teamId}/notion/databases`);
  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(toNotionError(detail, response.status));
  }

  const payload = (await response.json()) as NotionDatabasesResponse;
  if (!Array.isArray(payload.databases)) {
    throw new Error('Notionデータベース一覧を取得できませんでした。');
  }
  return payload.databases;
}

export async function updateNotionDefaultDatabase(
  user: User,
  teamId: string,
  databaseId: string,
  databaseName: string | null,
): Promise<NotionConnectionStatus> {
  const response = await fetchWithAuth(user, `/api/teams/${teamId}/notion/default-database`, {
    method: 'PATCH',
    body: JSON.stringify({
      database_id: databaseId,
      database_name: databaseName,
    }),
  });
  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(toNotionError(detail, response.status));
  }

  const payload = (await response.json()) as NotionConnectionResponse;
  if (!payload.connection) {
    throw new Error('Notion既定データベースを保存できませんでした。');
  }
  return payload.connection;
}

function toNotionError(detail: string, status: number) {
  if (status === 401 || detail === 'authorization bearer token is required') {
    return 'ログイン状態を確認してください。';
  }
  if (status === 403) {
    return 'このチームにアクセスできません。';
  }
  if (detail === 'notion oauth is not configured') {
    return 'Notion OAuth設定が完了していません。';
  }
  if (detail === 'notion connection not found') {
    return 'Notion連携がまだ完了していません。';
  }
  if (detail === 'database_id is required') {
    return 'Notionデータベースを選択してください。';
  }
  if (detail === 'failed to fetch notion databases') {
    return 'Notionデータベース一覧の取得に失敗しました。';
  }
  return 'Notion連携の処理に失敗しました。時間をおいて再試行してください。';
}
