import type { User } from 'firebase/auth';

import { fetchWithAuth, readErrorDetail } from './http';

export type TaskStatus = 'todo' | 'in_progress' | 'done';
type ApiTaskStatus = TaskStatus | 'doing';
export type RoadmapGenerationStatus = 'pending' | 'generating' | 'ready' | 'failed';

export type RoadmapStep = {
  id: string;
  title: string;
  description: string;
  status: TaskStatus;
  position: number;
  source: 'ai' | 'user';
  user_edited: boolean;
};

export type TaskRoadmap = {
  id: string;
  overview: string;
  generation_status: RoadmapGenerationStatus;
  generation_error: string | null;
  generation_started_at: number | null;
  version: number;
  has_source_updates: boolean;
  steps: RoadmapStep[];
};

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
  roadmap: TaskRoadmap | null;
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

export type RoadmapSaveStepInput = {
  id?: string;
  title: string;
  description: string;
  status: TaskStatus;
};

export type RoadmapSaveInput = {
  expected_version: number;
  steps: RoadmapSaveStepInput[];
};

export type TaskCreateInput = {
  title: string;
  body?: string;
  assignee_user_id?: string | null;
  status: TaskStatus;
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
  status: ApiTaskStatus;
  due_at?: string | null;
  created_at: string;
  updated_at: string;
  roadmap?: ApiTaskRoadmap | null;
};

type ApiRoadmapStep = {
  id: string;
  title: string;
  description: string;
  status: ApiTaskStatus;
  position: number;
  source: 'ai' | 'user';
  user_edited: boolean;
};

type ApiTaskRoadmap = {
  id: string;
  overview: string;
  generation_status: RoadmapGenerationStatus;
  generation_error?: string | null;
  generation_started_at?: string | null;
  version: number;
  has_source_updates: boolean;
  steps?: ApiRoadmapStep[];
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

export async function fetchMinutesTasks(
  user: User,
  teamId: string,
  minutesId: string,
): Promise<TeamTaskSummary[]> {
  const response = await fetchWithAuth(
    user,
    `/api/teams/${teamId}/minutes/${minutesId}/tasks`,
  );
  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(toTaskError(detail, response.status));
  }

  const payload = (await response.json()) as ApiTaskListResponse;
  if (!Array.isArray(payload.tasks)) {
    throw new Error('議事録の関連タスクAPIのレスポンスを読み取れませんでした。');
  }

  return payload.tasks.map(toTaskSummary);
}

export async function createTeamTask(
  user: User,
  teamId: string,
  input: TaskCreateInput,
): Promise<TeamTaskSummary> {
  const response = await fetchWithAuth(user, `/api/teams/${teamId}/tasks`, {
    method: 'POST',
    body: JSON.stringify(input),
  });
  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(toTaskError(detail, response.status));
  }

  const payload = (await response.json()) as ApiTaskResponse;
  if (!payload.task) {
    throw new Error('タスク作成APIのレスポンスを読み取れませんでした。');
  }

  return toTaskSummary(payload.task);
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

export async function generateTaskRoadmap(
  user: User,
  taskId: string,
  reopen = false,
  expectedVersion?: number,
  forceRegenerate = false,
): Promise<TeamTaskSummary> {
  return mutateTaskRoadmap(user, taskId, '/roadmap/generate', 'POST', {
    reopen,
    expected_version: expectedVersion,
    force_regenerate: forceRegenerate,
  });
}

export async function saveTaskRoadmap(
  user: User,
  taskId: string,
  input: RoadmapSaveInput,
): Promise<TeamTaskSummary> {
  return mutateTaskRoadmap(user, taskId, '/roadmap', 'PUT', input);
}

export async function createRoadmapStep(
  user: User,
  taskId: string,
  input: { title: string; description: string },
  expectedVersion?: number,
): Promise<TeamTaskSummary> {
  return mutateTaskRoadmap(user, taskId, '/roadmap/steps', 'POST', {
    ...input,
    expected_version: expectedVersion,
  });
}

export async function updateRoadmapStep(
  user: User,
  taskId: string,
  stepId: string,
  input: {
    title?: string;
    description?: string;
    status?: TaskStatus;
    reopen_task?: boolean;
  },
  expectedVersion?: number,
): Promise<TeamTaskSummary> {
  return mutateTaskRoadmap(
    user,
    taskId,
    `/roadmap/steps/${stepId}`,
    'PATCH',
    { ...input, expected_version: expectedVersion },
  );
}

export async function deleteRoadmapStep(
  user: User,
  taskId: string,
  stepId: string,
  expectedVersion?: number,
): Promise<TeamTaskSummary> {
  const versionQuery =
    expectedVersion === undefined
      ? ''
      : `?expected_version=${encodeURIComponent(expectedVersion)}`;
  return mutateTaskRoadmap(
    user,
    taskId,
    `/roadmap/steps/${stepId}${versionQuery}`,
    'DELETE',
  );
}

export async function reorderRoadmapSteps(
  user: User,
  taskId: string,
  stepIds: string[],
  expectedVersion?: number,
): Promise<TeamTaskSummary> {
  return mutateTaskRoadmap(user, taskId, '/roadmap/reorder', 'PUT', {
    step_ids: stepIds,
    expected_version: expectedVersion,
  });
}

async function mutateTaskRoadmap(
  user: User,
  taskId: string,
  path: string,
  method: 'POST' | 'PATCH' | 'PUT' | 'DELETE',
  body?: object,
): Promise<TeamTaskSummary> {
  const response = await fetchWithAuth(user, `/api/tasks/${taskId}${path}`, {
    method,
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(toTaskError(detail, response.status));
  }
  const payload = (await response.json()) as ApiTaskResponse;
  if (!payload.task) {
    throw new Error('ロードマップAPIのレスポンスを読み取れませんでした。');
  }
  return toTaskSummary(payload.task);
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
    status: normalizeTaskStatus(task.status),
    due_at: task.due_at ? toTimestamp(task.due_at) : null,
    created_at: toTimestamp(task.created_at),
    updated_at: toTimestamp(task.updated_at),
    roadmap: task.roadmap ? toTaskRoadmap(task.roadmap) : null,
  };
}

function toTaskRoadmap(roadmap: ApiTaskRoadmap): TaskRoadmap {
  return {
    id: roadmap.id,
    overview: roadmap.overview,
    generation_status: roadmap.generation_status,
    generation_error: roadmap.generation_error ?? null,
    generation_started_at:
      roadmap.generation_started_at === undefined
        ? Date.now()
        : roadmap.generation_started_at
          ? toTimestamp(roadmap.generation_started_at)
          : null,
    version: roadmap.version,
    has_source_updates: roadmap.has_source_updates,
    steps: Array.isArray(roadmap.steps)
      ? roadmap.steps
          .map((step) => ({
            id: step.id,
            title: step.title,
            description: step.description,
            status: normalizeTaskStatus(step.status),
            position: step.position,
            source: step.source,
            user_edited: step.user_edited,
          }))
          .sort((a, b) => a.position - b.position)
      : [],
  };
}

function normalizeTaskStatus(status: ApiTaskStatus): TaskStatus {
  return status === 'doing' ? 'in_progress' : status;
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
  if (detail === 'roadmap was updated by another user') {
    return 'ロードマップが別の操作で更新されました。画面を再読み込みしてから再試行してください。';
  }
  if (detail === 'roadmap generation is in progress') {
    return 'ロードマップ生成中は変更できません。生成完了後に再試行してください。';
  }
  if (
    detail === 'completed task must be reopened before roadmap generation' ||
    detail === 'reopen_task is required' ||
    detail === 'reopen the task through its roadmap'
  ) {
    return '完了済みタスクは、ロードマップから再オープンしてください。';
  }
  if (status === 404) {
    return '対象のタスクまたは議事録が見つかりません。';
  }
  if (detail === 'failed to generate tasks') {
    return 'タスク生成に失敗しました。時間をおいて再試行してください。';
  }
  if (detail === 'task title is required') {
    return 'タスクタイトルを入力してください。';
  }
  if (detail === 'assignee_user_id must be a team member') {
    return '担当者はチームメンバーから選択してください。';
  }
  if (detail === 'invalid task status') {
    return 'タスクステータスが不正です。';
  }
  return 'タスク処理に失敗しました。時間をおいて再試行してください。';
}
