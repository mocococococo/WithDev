import type { User } from 'firebase/auth';

import { fetchWithAuth, readErrorDetail } from './http';

export type TaskStatus = 'todo' | 'doing' | 'done';

export type TeamTaskSummary = {
  id: string;
  team_id: string;
  source_minutes_id: string | null;
  title: string;
  body: string;
  assignee_user_id: string | null;
  assignee_name: string | null;
  status: TaskStatus;
  due_at: number | null;
  created_at: number;
  updated_at: number;
};

export type TeamMemberSummary = {
  user_id: string;
  display_name: string;
  email: string;
  role: string;
};

export type TaskUpdateInput = {
  title?: string;
  body?: string;
  assignee_user_id?: string | null;
  assignee_name?: string | null;
  status?: TaskStatus;
  due_at?: string | null;
};

type ApiTask = {
  id: string;
  team_id: string;
  source_minutes_id?: string | null;
  title: string;
  body: string;
  assignee_user_id?: string | null;
  assignee_name?: string | null;
  status: TaskStatus;
  due_at?: string | null;
  created_at: string;
  updated_at: string;
};

type ApiTaskListResponse = {
  tasks?: ApiTask[];
};

type ApiTaskResponse = {
  task?: ApiTask;
};

type ApiTeamMember = {
  user_id: string;
  display_name: string;
  email: string;
  role: string;
};

type ApiTeamMemberListResponse = {
  members?: ApiTeamMember[];
};

export async function fetchTeamMembers(
  user: User,
  teamId: string,
): Promise<TeamMemberSummary[]> {
  const response = await fetchWithAuth(user, `/api/teams/${teamId}/members`);
  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(toTaskError(detail, response.status));
  }

  const payload = (await response.json()) as ApiTeamMemberListResponse;
  if (!Array.isArray(payload.members)) {
    throw new Error('チームメンバー一覧APIのレスポンスを読み取れませんでした。');
  }

  return payload.members;
}

export async function fetchTeamTasks(
  user: User,
  teamId: string,
  assignee?: 'me',
): Promise<TeamTaskSummary[]> {
  const query = assignee ? `?assignee=${encodeURIComponent(assignee)}` : '';
  const response = await fetchWithAuth(user, `/api/teams/${teamId}/tasks${query}`);
  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(toTaskError(detail, response.status));
  }

  const payload = (await response.json()) as ApiTaskListResponse;
  if (!Array.isArray(payload.tasks)) {
    throw new Error('タスク一覧APIのレスポンスを読み取れませんでした。');
  }

  return payload.tasks.map(toTaskSummary);
}

export async function generateTeamTasks(
  user: User,
  teamId: string,
  minutesId: string,
): Promise<TeamTaskSummary[]> {
  const response = await fetchWithAuth(user, `/api/teams/${teamId}/tasks/generate`, {
    method: 'POST',
    body: JSON.stringify({ minutes_id: minutesId }),
  });
  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(toTaskError(detail, response.status));
  }

  const payload = (await response.json()) as ApiTaskListResponse;
  if (!Array.isArray(payload.tasks)) {
    throw new Error('タスク生成APIのレスポンスを読み取れませんでした。');
  }

  return payload.tasks.map(toTaskSummary);
}

export async function fetchTask(user: User, taskId: string): Promise<TeamTaskSummary> {
  const response = await fetchWithAuth(user, `/api/tasks/${taskId}`);
  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(toTaskError(detail, response.status));
  }

  const payload = (await response.json()) as ApiTaskResponse;
  if (!payload.task) {
    throw new Error('タスク詳細APIのレスポンスを読み取れませんでした。');
  }

  return toTaskSummary(payload.task);
}

export async function updateTask(
  user: User,
  taskId: string,
  input: TaskUpdateInput,
): Promise<TeamTaskSummary> {
  const response = await fetchWithAuth(user, `/api/tasks/${taskId}`, {
    method: 'PATCH',
    body: JSON.stringify(input),
  });
  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(toTaskError(detail, response.status));
  }

  const payload = (await response.json()) as ApiTaskResponse;
  if (!payload.task) {
    throw new Error('タスク更新APIのレスポンスを読み取れませんでした。');
  }

  return toTaskSummary(payload.task);
}

export async function deleteTask(user: User, taskId: string): Promise<void> {
  const response = await fetchWithAuth(user, `/api/tasks/${taskId}`, {
    method: 'DELETE',
  });
  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(toTaskError(detail, response.status));
  }
}

function toTaskSummary(task: ApiTask): TeamTaskSummary {
  return {
    id: task.id,
    team_id: task.team_id,
    source_minutes_id: task.source_minutes_id ?? null,
    title: task.title,
    body: task.body,
    assignee_user_id: task.assignee_user_id ?? null,
    assignee_name: task.assignee_name ?? null,
    status: task.status,
    due_at: task.due_at ? toTimestamp(task.due_at) : null,
    created_at: toTimestamp(task.created_at),
    updated_at: toTimestamp(task.updated_at),
  };
}

function toTimestamp(value: string) {
  const timestamp = Date.parse(value);
  return Number.isNaN(timestamp) ? Date.now() : timestamp;
}

function toTaskError(detail: string, status: number) {
  if (status === 401 || detail === 'authorization bearer token is required') {
    return 'ログイン状態を確認してください。';
  }
  if (status === 403) {
    return 'このチームにアクセスできません。';
  }
  if (status === 404) {
    return '対象のタスクまたは議事録が見つかりません。';
  }
  if (detail === 'failed to generate tasks') {
    return 'タスク生成に失敗しました。時間をおいて再試行してください。';
  }
  if (detail === 'assignee_user_id must be a team member') {
    return '担当者はチームメンバーから選択してください。';
  }
  if (detail === 'invalid task status') {
    return 'タスクステータスが不正です。';
  }
  return 'タスク処理に失敗しました。時間をおいて再試行してください。';
}
