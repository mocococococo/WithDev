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

export type WorkspaceUserSummary = {
  id: string;
  firebase_uid: string;
  email: string;
  display_name: string;
  photo_url: string | null;
};

export type WorkspaceContext = {
  user: WorkspaceUserSummary;
  teams: UserTeamSummary[];
};

export type MeetingTheme = {
  id?: string;
  title?: string;
  created_at?: number;
};

export type MeetingSummary = {
  id: string;
  team_id: string;
  title: string;
  themes: MeetingTheme[] | null;
  initial_theme: string;
  status: MeetingStatus;
  participant_count: number;
  created_at: number;
  updated_at: number;
  ended_at: number | null;
  minutes_id: string | null;
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
  member_count: number;
};

type ApiMeResponse = {
  user?: WorkspaceUserSummary;
  teams?: ApiTeam[];
};

type ApiMeeting = {
  id: string;
  team_id: string;
  title: string;
  themes?: MeetingTheme[] | null;
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

type ApiMeetingCreateResponse = ApiMeetingResponse & {
  launch_url?: string;
};

export type MeetingLaunch = {
  meeting: MeetingSummary;
  launch_url: string;
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

export async function fetchMe(user: User): Promise<WorkspaceContext> {
  const response = await fetchWithAuth(user, '/api/me');
  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(toWorkspaceError(detail, response.status));
  }

  const payload = (await response.json()) as ApiMeResponse;
  if (!payload.user || !Array.isArray(payload.teams)) {
    throw new Error('ユーザー初期化APIのレスポンスを読み取れませんでした。');
  }

  return {
    user: payload.user,
    teams: payload.teams.map((team) => ({
      team_id: team.id,
      name: team.name,
      role: team.role,
      member_count: team.member_count,
    })),
  };
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
): Promise<MeetingLaunch> {
  const response = await fetchWithAuth(user, `/api/teams/${teamId}/meetings`, {
    method: 'POST',
    body: JSON.stringify({ title, theme: initialTheme }),
  });
  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(toWorkspaceError(detail, response.status));
  }

  const payload = (await response.json()) as ApiMeetingCreateResponse;
  if (!payload.meeting || !payload.launch_url) {
    throw new Error('ミーティング作成APIのレスポンスを読み取れませんでした。');
  }

  return {
    meeting: toMeetingSummary(payload.meeting),
    launch_url: payload.launch_url,
  };
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
    themes: meeting.themes ?? null,
    initial_theme: meeting.themes?.[0]?.title ?? '',
    status: meeting.status,
    participant_count: meeting.participant_count ?? 1,
    created_at: toTimestamp(meeting.created_at),
    updated_at: toTimestamp(meeting.updated_at),
    ended_at: meeting.ended_at ? toTimestamp(meeting.ended_at) : null,
    minutes_id: null,
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
  if (
    detail === 'aiboard api is not configured' ||
    detail === 'aiboard api key is not configured' ||
    detail === 'aiboard frontend is not configured'
  ) {
    return 'Aiboard連携が設定されていません。';
  }
  if (
    detail === 'aiboard api key was rejected' ||
    detail === 'failed to create aiboard meeting' ||
    detail === 'invalid aiboard response' ||
    detail === 'aiboard response team does not match'
  ) {
    return 'Aiboardでミーティングを作成できませんでした。';
  }
  return '処理に失敗しました。時間をおいて再試行してください。';
}
