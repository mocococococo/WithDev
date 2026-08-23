import type { User } from 'firebase/auth';
import { fetchWithAuth, readErrorDetail } from './http';
import type { UserTeamSummary } from './workspace';

export type TeamInviteStatus = 'active' | 'expired' | 'revoked';

export type TeamInvite = {
  id: string;
  team_id: string;
  created_by: {
    id: string;
    name: string;
  };
  created_at: string;
  expires_at: string;
  status: TeamInviteStatus;
  can_revoke: boolean;
};

export type InvitePreview = {
  team_id: string;
  team_name: string;
  expires_at: string;
  already_member: boolean;
};

export async function createTeam(user: User, name: string): Promise<UserTeamSummary> {
  const response = await fetchWithAuth(user, '/api/teams', {
    method: 'POST',
    body: JSON.stringify({ name }),
  });
  if (!response.ok) throw new Error(await teamError(response));
  const payload = (await response.json()) as { team?: ApiTeam };
  if (!payload.team) throw new Error('チーム作成APIの応答を読み取れませんでした。');
  return toTeam(payload.team);
}

export async function createDemoTeam(user: User): Promise<UserTeamSummary> {
  const response = await fetchWithAuth(user, '/api/teams/demo', { method: 'POST' });
  if (!response.ok) throw new Error(await teamError(response));
  const payload = (await response.json()) as { team?: ApiTeam };
  if (!payload.team) throw new Error('デモモードAPIの応答を読み取れませんでした。');
  return toTeam(payload.team);
}

export async function fetchTeamInvites(user: User, teamId: string): Promise<TeamInvite[]> {
  const response = await fetchWithAuth(user, `/api/teams/${teamId}/invites`);
  if (!response.ok) throw new Error(await teamError(response));
  const payload = (await response.json()) as { invites?: TeamInvite[] };
  if (!Array.isArray(payload.invites)) throw new Error('招待一覧APIの応答を読み取れませんでした。');
  return payload.invites;
}

export async function createTeamInvite(
  user: User,
  teamId: string,
): Promise<{ invite: TeamInvite; inviteUrl: string }> {
  const response = await fetchWithAuth(user, `/api/teams/${teamId}/invites`, { method: 'POST' });
  if (!response.ok) throw new Error(await teamError(response));
  const payload = (await response.json()) as { invite?: TeamInvite; invite_url?: string };
  if (!payload.invite || !payload.invite_url) throw new Error('招待作成APIの応答を読み取れませんでした。');
  return { invite: payload.invite, inviteUrl: payload.invite_url };
}

export async function revokeTeamInvite(user: User, teamId: string, inviteId: string): Promise<void> {
  const response = await fetchWithAuth(user, `/api/teams/${teamId}/invites/${inviteId}`, { method: 'DELETE' });
  if (!response.ok) throw new Error(await teamError(response));
}

export async function fetchInvitePreview(user: User, token: string): Promise<InvitePreview> {
  const response = await fetchWithAuth(user, `/api/invites/${encodeURIComponent(token)}`);
  if (!response.ok) throw new Error(await teamError(response));
  return (await response.json()) as InvitePreview;
}

export async function acceptTeamInvite(user: User, token: string): Promise<UserTeamSummary> {
  const response = await fetchWithAuth(user, `/api/invites/${encodeURIComponent(token)}/accept`, { method: 'POST' });
  if (!response.ok) throw new Error(await teamError(response));
  const payload = (await response.json()) as { team?: ApiTeam };
  if (!payload.team) throw new Error('招待参加APIの応答を読み取れませんでした。');
  return toTeam(payload.team);
}

type ApiTeam = {
  id: string;
  name: string;
  role: UserTeamSummary['role'];
  member_count: number;
};

function toTeam(team: ApiTeam): UserTeamSummary {
  return {
    team_id: team.id,
    name: team.name,
    role: team.role,
    member_count: team.member_count,
  };
}

async function teamError(response: Response): Promise<string> {
  const detail = await readErrorDetail(response);
  if (detail === 'team name is required') return 'チーム名を入力してください。';
  if (detail === 'team name is too long') return 'チーム名は255文字以内で入力してください。';
  if (detail === 'invite is expired') return 'この招待リンクの有効期限は切れています。';
  if (detail === 'invite is revoked') return 'この招待リンクは無効化されています。';
  if (detail === 'invite not found') return '招待リンクが見つかりません。';
  if (detail === 'host email is required') return 'ログインユーザーのメールアドレスを確認できませんでした。';
  if (detail === 'aiboard api is not configured') return 'ミーティング作成機能が設定されていません。';
  if (detail === 'aiboard api key was rejected') return 'ミーティング作成機能の認証に失敗しました。';
  if (detail.startsWith('aiboard request validation failed')) return 'デモミーティングを作成できませんでした。';
  if (detail === 'failed to create aiboard meeting' || detail === 'invalid aiboard response') {
    return 'デモミーティングを作成できませんでした。';
  }
  if (response.status === 403) return 'この操作を行う権限がありません。';
  return '処理に失敗しました。時間をおいて再試行してください。';
}
