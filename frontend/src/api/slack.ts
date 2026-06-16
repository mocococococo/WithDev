import type { User } from 'firebase/auth';
import { fetchWithAuth, readErrorDetail } from './http';
import type { MeetingMinutesSummary } from './workspace';

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

export type SlackConnectionStatus = {
  connected: boolean;
  slack_team_id: string | null;
  slack_team_name: string | null;
  default_channel_id: string | null;
  default_channel_name: string | null;
};

type SlackOAuthStartResponse = {
  url?: string;
};

type SlackConnectionResponse = {
  connection?: SlackConnectionStatus;
};

type SlackChannelsResponse = {
  channels?: SlackChannel[];
};

type SlackPostResponse = {
  slack_post?: SlackPost;
};

type ApiMinutes = {
  id: string;
  meeting_id: string;
  title?: string | null;
  body: string;
  created_at: string;
  updated_at: string;
};

type MinutesToSlackResponse = {
  minutes?: ApiMinutes;
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

export async function fetchSlackConnection(
  user: User,
  teamId: string,
): Promise<SlackConnectionStatus> {
  const response = await fetchWithAuth(user, `/api/teams/${teamId}/slack/connection`);
  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(toSlackError(detail, response.status));
  }

  const payload = (await response.json()) as SlackConnectionResponse;
  if (!payload.connection) {
    throw new Error('Slack連携状態を取得できませんでした。');
  }
  return payload.connection;
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

export async function updateSlackDefaultChannel(
  user: User,
  teamId: string,
  channelId: string,
  channelName: string | null,
): Promise<SlackConnectionStatus> {
  const response = await fetchWithAuth(user, `/api/teams/${teamId}/slack/default-channel`, {
    method: 'PATCH',
    body: JSON.stringify({
      channel_id: channelId,
      channel_name: channelName,
    }),
  });
  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(toSlackError(detail, response.status));
  }

  const payload = (await response.json()) as SlackConnectionResponse;
  if (!payload.connection) {
    throw new Error('Slack既定チャンネルを保存できませんでした。');
  }
  return payload.connection;
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

export async function generateMeetingMinutesToSlack(
  user: User,
  meetingId: string,
  text: string,
  channelId: string,
): Promise<{ minutes: MeetingMinutesSummary; slackPost: SlackPost }> {
  const response = await fetchWithAuth(user, `/api/meetings/${meetingId}/minutes_to_slack`, {
    method: 'POST',
    body: JSON.stringify({ text, channel_id: channelId }),
  });
  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(toSlackError(detail, response.status));
  }

  const payload = (await response.json()) as MinutesToSlackResponse;
  if (!payload.minutes || !payload.slack_post) {
    throw new Error('Slack投稿付き議事録生成APIのレスポンスを読み取れませんでした。');
  }

  return {
    minutes: toMinutesSummary(payload.minutes),
    slackPost: payload.slack_post,
  };
}

function toMinutesSummary(minutes: ApiMinutes): MeetingMinutesSummary {
  return {
    id: minutes.id,
    meeting_id: minutes.meeting_id,
    title: minutes.title ?? null,
    body: minutes.body,
    created_at: toTimestamp(minutes.created_at),
    updated_at: toTimestamp(minutes.updated_at),
  };
}

function toTimestamp(value: string) {
  const timestamp = Date.parse(value);
  return Number.isNaN(timestamp) ? Date.now() : timestamp;
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
