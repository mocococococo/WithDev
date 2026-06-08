import type { User } from 'firebase/auth';
import { fetchWithAuth, readErrorDetail } from './http';

export type SlackChannel = {
  id: string;
  name: string;
  is_private: boolean;
};

export type SlackPost = {
  id: string;
  minutes_id: string;
  channel_id: string;
  channel_name: string | null;
  slack_ts: string | null;
  status: 'success' | 'failed';
  created_at: string;
};

type SlackOAuthStartResponse = {
  url?: string;
};

type SlackChannelsResponse = {
  channels?: SlackChannel[];
};

type SlackPostResponse = {
  slack_post?: SlackPost;
};

export async function startSlackOAuth(user: User, teamId: string): Promise<string> {
  const response = await fetchWithAuth(user, `/api/teams/${teamId}/slack/oauth/start`);
  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(toSlackError(detail, response.status));
  }

  const payload = (await response.json()) as SlackOAuthStartResponse;
  if (typeof payload.url !== 'string') {
    throw new Error('Slack連携URLを取得できませんでした。');
  }
  return payload.url;
}

export async function fetchSlackChannels(user: User, teamId: string): Promise<SlackChannel[]> {
  const response = await fetchWithAuth(user, `/api/teams/${teamId}/slack/channels`);
  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(toSlackError(detail, response.status));
  }

  const payload = (await response.json()) as SlackChannelsResponse;
  if (!Array.isArray(payload.channels)) {
    throw new Error('Slackチャンネル一覧を取得できませんでした。');
  }
  return payload.channels;
}

export async function postMinutesToSlack(
  user: User,
  minutesId: string,
  channelId: string,
): Promise<SlackPost> {
  const response = await fetchWithAuth(user, `/api/minutes/${minutesId}/slack-posts`, {
    method: 'POST',
    body: JSON.stringify({ channel_id: channelId }),
  });
  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(toSlackError(detail, response.status));
  }

  const payload = (await response.json()) as SlackPostResponse;
  if (!payload.slack_post) {
    throw new Error('Slack投稿結果を取得できませんでした。');
  }
  return payload.slack_post;
}

function toSlackError(detail: string, status: number) {
  if (status === 401 || detail === 'authorization bearer token is required') {
    return 'ログイン状態を確認してください。';
  }
  if (status === 403) {
    return 'このチームにアクセスできません。';
  }
  if (detail === 'slack connection not found') {
    return 'Slack連携がまだ完了していません。';
  }
  if (detail === 'channel_id is required') {
    return '投稿先チャンネルを選択してください。';
  }
  if (detail === 'failed to fetch slack channels') {
    return 'Slackチャンネル一覧の取得に失敗しました。';
  }
  if (detail === 'failed to post minutes to slack') {
    return 'Slackへの投稿に失敗しました。';
  }
  return 'Slack連携の処理に失敗しました。時間をおいて再試行してください。';
}
