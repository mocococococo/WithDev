import { useEffect, useRef, useState } from 'react';
import type { User } from 'firebase/auth';

import { fetchWithAuth, readErrorDetail } from '../api/http';
import { AssignmentStartButton } from './AssignmentStartButton';

type Assignment = {
  status: string;
  error: string | null;
  attempts: { user_name: string; status: string; expires_at: string | null }[];
};

const labels: Record<string, string> = {
  pending: '担当候補の選定待ち', selecting: '担当候補を選定中', ready: '次の候補への打診待ち',
  sending: '個別DMを送信中', waiting: '返答待ち', negotiating: '相談中',
  assigned: '担当決定', expired: '返答なし・未割り当て', exhausted: '候補への打診終了・未割り当て',
  cancelled: 'タスクやメンバー情報の変更により打診終了', failed: '担当調整の処理に失敗',
  accepted: '受諾', declined: '辞退',
};

export function TaskAssignmentPanel({ user, taskId, onAssigned }: {
  user: User; taskId: string; onAssigned: (taskId: string) => Promise<void>;
}) {
  const [assignment, setAssignment] = useState<Assignment | null>(null);
  const [error, setError] = useState('');
  const [canStart, setCanStart] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [refreshVersion, setRefreshVersion] = useState(0);
  const refreshedAssignment = useRef(false);
  useEffect(() => {
    let active = true;
    setIsLoading(true);
    const load = async () => {
      try {
        const response = await fetchWithAuth(user, `/api/tasks/${taskId}/assignment`);
        if (!response.ok) throw new Error(await readErrorDetail(response));
        const result = await response.json() as { assignment: Assignment | null; can_start: boolean };
        if (active) { setAssignment(result.assignment); setCanStart(result.can_start); setError(''); }
        if (active && result.assignment?.status === 'assigned' && !refreshedAssignment.current) {
          await onAssigned(taskId);
          refreshedAssignment.current = true;
        }
      } catch {
        if (active) setError('担当調整の状況を取得できませんでした。');
      } finally {
        if (active) setIsLoading(false);
      }
    };
    void load();
    const timer = window.setInterval(() => { void load(); }, 15000);
    return () => { active = false; window.clearInterval(timer); };
  }, [user, taskId, onAssigned, refreshVersion]);
  if (!assignment && !canStart && !error && !isLoading) return null;
  return (
    <section className="assignment-panel" aria-label="Slackでの担当調整">
      <div className="assignment-panel-heading">
        <h2>Slackでの担当調整</h2>
        <AssignmentStartButton
          user={user}
          target={{ kind: 'task', id: taskId }}
          label="割り当て打診"
          disabled={isLoading || !canStart}
          onStarted={() => {
            setCanStart(false);
            refreshedAssignment.current = false;
            setRefreshVersion((version) => version + 1);
          }}
        />
      </div>
      {isLoading && <p className="subtle-copy">担当調整の状況を確認しています。</p>}
      {error && <p className="error-text">{error}</p>}
      {assignment && <>
        <p role="status">{labels[assignment.status] ?? assignment.status}</p>
        {assignment.attempts.length > 0 && <ul>
          {assignment.attempts.map((attempt, index) => (
            <li key={index}>
              {attempt.user_name}：{labels[attempt.status] ?? attempt.status}
              {['waiting', 'negotiating'].includes(attempt.status) && attempt.expires_at && (
                <>（返答期限：{new Intl.DateTimeFormat('ja-JP', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(attempt.expires_at))}）</>
              )}
            </li>
          ))}
        </ul>}
        {assignment.error && <p className="error-text">{assignment.error}</p>}
      </>}
      <p className="subtle-copy">個別DMで打診します。返答がない場合は3日で終了し、再通知はしません。全員が辞退した場合も未割り当てのままになります。</p>
    </section>
  );
}
