import { useRef, useState } from 'react';
import type { User } from 'firebase/auth';
import { Loader2, UserCheck } from 'lucide-react';

import { startAssignments, type AssignmentTarget } from '../api/taskAssignments';

export function AssignmentStartButton({ user, target, label, description, disabled, onStarted }: {
  user: User;
  target: AssignmentTarget;
  label: string;
  description?: string;
  disabled?: boolean;
  onStarted?: () => void;
}) {
  const [isStarting, setIsStarting] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const inFlight = useRef(false);

  const start = async () => {
    if (inFlight.current || disabled) return;
    inFlight.current = true;
    setIsStarting(true);
    setMessage('');
    setError('');
    try {
      const result = await startAssignments(user, target);
      const skipped = result.skipped_count > 0 ? ` ${result.skipped_count}件は打診中または対象外のためスキップしました。` : '';
      setMessage(result.started_count > 0
        ? `${result.started_count}件の割り当て打診を受け付けました。${skipped}`
        : `新しく打診するタスクはありません。${skipped}`);
      onStarted?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : '割り当て打診を開始できませんでした。');
    } finally {
      inFlight.current = false;
      setIsStarting(false);
    }
  };

  return (
    <div className="assignment-start-action">
      <button className="secondary-button" type="button" disabled={disabled || isStarting} onClick={() => void start()}>
        {isStarting ? <Loader2 className="spin" size={18} /> : <UserCheck size={18} />}
        {isStarting ? '受付中…' : label}
      </button>
      {description && <p className="assignment-start-message subtle-copy">{description}</p>}
      {message && <p className="assignment-start-message success-text" role="status">{message}</p>}
      {error && <p className="assignment-start-message error-text" role="alert">{error}</p>}
    </div>
  );
}
