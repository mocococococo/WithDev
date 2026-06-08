import type { User } from 'firebase/auth';
import { fetchWithAuth, readErrorDetail } from './http';

export type TeamRole = 'owner' | 'admin' | 'member';
export type MeetingStatus = 'active' | 'ended';

export type UserTeamSummary = {
  team_id: string;
  name: string;
  role: TeamRole;
  member_count: number;
};

export type MeetingSummary = {
  id: string;
  team_id: string;
  title: string;
  initial_theme: string;
  status: MeetingStatus;
  participant_count: number;
  created_at: number;
  updated_at: number;
  ended_at: number | null;
  minutes: string | null;
};

export type MeetingMinutesSummary = {
  id: string;
  meeting_id: string;
  title: string | null;
  body: string;
  created_at: number;
  updated_at: number;
};

type ApiTeam = {
  id: string;
  name: string;
  role: TeamRole;
};

type ApiMeResponse = {
  teams?: ApiTeam[];
};

type ApiMeeting = {
  id: string;
  team_id: string;
  title: string;
  theme?: string | null;
  status: MeetingStatus;
  started_at: string;
  ended_at?: string | null;
  created_at: string;
  updated_at: string;
  participant_count?: number;
};

type ApiMeetingListResponse = {
  meetings?: ApiMeeting[];
};

type ApiMeetingResponse = {
  meeting?: ApiMeeting;
};

type ApiMinutes = {
  id: string;
  meeting_id: string;
  title?: string | null;
  body: string;
  created_at: string;
  updated_at: string;
};

type ApiMinutesResponse = {
  minutes?: ApiMinutes;
};

export async function fetchMe(user: User): Promise<UserTeamSummary[]> {
  const response = await fetchWithAuth(user, '/api/me');
  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(toWorkspaceError(detail, response.status));
  }

  const payload = (await response.json()) as ApiMeResponse;
  if (!Array.isArray(payload.teams)) {
    throw new Error('ユーザー初期化APIのレスポンスを読み取れませんでした。');
  }

  return payload.teams.map((team) => ({
    team_id: team.id,
    name: team.name,
    role: team.role,
    member_count: 1,
  }));
}

export async function fetchTeamMeetings(user: User, teamId: string): Promise<MeetingSummary[]> {
  const response = await fetchWithAuth(user, `/api/teams/${teamId}/meetings`);
  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(toWorkspaceError(detail, response.status));
  }

  const payload = (await response.json()) as ApiMeetingListResponse;
  if (!Array.isArray(payload.meetings)) {
    throw new Error('ミーティング一覧APIのレスポンスを読み取れませんでした。');
  }

  return payload.meetings.map(toMeetingSummary);
}

export async function createTeamMeeting(
  user: User,
  teamId: string,
  title: string,
  initialTheme: string,
): Promise<MeetingSummary> {
  const response = await fetchWithAuth(user, `/api/teams/${teamId}/meetings`, {
    method: 'POST',
    body: JSON.stringify({ title, theme: initialTheme }),
  });
  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(toWorkspaceError(detail, response.status));
  }

  const payload = (await response.json()) as ApiMeetingResponse;
  if (!payload.meeting) {
    throw new Error('ミーティング作成APIのレスポンスを読み取れませんでした。');
  }

  return toMeetingSummary(payload.meeting);
}

export async function fetchMeetingDetail(user: User, meetingId: string): Promise<MeetingSummary> {
  const response = await fetchWithAuth(user, `/api/meetings/${meetingId}`);
  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(toWorkspaceError(detail, response.status));
  }

  const payload = (await response.json()) as ApiMeetingResponse;
  if (!payload.meeting) {
    throw new Error('ミーティング詳細APIのレスポンスを読み取れませんでした。');
  }

  return toMeetingSummary(payload.meeting);
}

export async function fetchMeetingMinutes(
  user: User,
  meetingId: string,
): Promise<MeetingMinutesSummary | null> {
  const response = await fetchWithAuth(user, `/api/meetings/${meetingId}/minutes`);
  if (response.status === 404) {
    return null;
  }
  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(toWorkspaceError(detail, response.status));
  }

  const payload = (await response.json()) as ApiMinutesResponse;
  if (!payload.minutes) {
    throw new Error('議事録取得APIのレスポンスを読み取れませんでした。');
  }

  return toMinutesSummary(payload.minutes);
}

export async function generateMeetingMinutesFromText(
  user: User,
  meetingId: string,
  text: string,
): Promise<MeetingMinutesSummary> {
  const response = await fetchWithAuth(user, `/api/meetings/${meetingId}/minutes/from-text`, {
    method: 'POST',
    body: JSON.stringify({ text }),
  });
  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(toWorkspaceError(detail, response.status));
  }

  const payload = (await response.json()) as ApiMinutesResponse;
  if (!payload.minutes) {
    throw new Error('議事録生成APIのレスポンスを読み取れませんでした。');
  }

  return toMinutesSummary(payload.minutes);
}

function toMeetingSummary(meeting: ApiMeeting): MeetingSummary {
  return {
    id: meeting.id,
    team_id: meeting.team_id,
    title: meeting.title,
    initial_theme: meeting.theme ?? '',
    status: meeting.status,
    participant_count: meeting.participant_count ?? 1,
    created_at: toTimestamp(meeting.created_at),
    updated_at: toTimestamp(meeting.updated_at),
    ended_at: meeting.ended_at ? toTimestamp(meeting.ended_at) : null,
    minutes: null,
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

function toWorkspaceError(detail: string, status: number) {
  if (status === 401 || detail === 'authorization bearer token is required') {
    return 'ログイン状態を確認してください。';
  }
  if (status === 403) {
    return 'このチームにアクセスできません。';
  }
  if (status === 404) {
    return '対象データが見つかりません。';
  }
  if (detail === 'title is required') {
    return 'ミーティングタイトルを入力してください。';
  }
  if (detail === 'text is required') {
    return '文字起こしを入力してください。';
  }
  if (detail === 'text must be 50000 characters or less') {
    return '文字起こしは50,000文字以内にしてください。';
  }
  if (detail === 'failed to generate minutes') {
    return '議事録の生成に失敗しました。時間をおいて再試行してください。';
  }
  return '処理に失敗しました。時間をおいて再試行してください。';
}
