import type { User } from 'firebase/auth';

import { fetchWithAuth, readErrorDetail } from './http';

export type AssignmentStartResult = { started_count: number; skipped_count: number };
export type AssignmentTarget = { kind: 'team' | 'task'; id: string };

export async function startAssignments(user: User, target: AssignmentTarget): Promise<AssignmentStartResult> {
  const path = target.kind === 'team'
    ? `/api/teams/${target.id}/assignments/start`
    : `/api/tasks/${target.id}/assignment/start`;
  const response = await fetchWithAuth(user, path, { method: 'POST' });
  if (!response.ok) throw new Error(await readErrorDetail(response));
  return response.json() as Promise<AssignmentStartResult>;
}
