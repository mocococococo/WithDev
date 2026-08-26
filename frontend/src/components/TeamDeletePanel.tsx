import { Loader2, Trash2, X } from 'lucide-react';
import type { FormEvent, MouseEvent } from 'react';
import { useEffect, useState } from 'react';

type TeamDeletePanelProps = {
  teamName: string;
  onDelete: (confirmationName: string) => Promise<void>;
};

export function TeamDeletePanel({ teamName, onDelete }: TeamDeletePanelProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [confirmationName, setConfirmationName] = useState('');
  const [isDeleting, setIsDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canDelete = confirmationName === teamName && !isDeleting;

  useEffect(() => {
    setIsOpen(false);
    setConfirmationName('');
    setError(null);
  }, [teamName]);

  useEffect(() => {
    if (!isOpen) return;

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !isDeleting) {
        setIsOpen(false);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isDeleting, isOpen]);

  const openConfirmation = () => {
    setConfirmationName('');
    setError(null);
    setIsOpen(true);
  };

  const closeConfirmation = () => {
    if (isDeleting) return;
    setIsOpen(false);
    setConfirmationName('');
    setError(null);
  };

  const handleBackdropClick = (event: MouseEvent<HTMLDivElement>) => {
    if (event.target === event.currentTarget) closeConfirmation();
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canDelete) return;

    setError(null);
    setIsDeleting(true);
    try {
      await onDelete(confirmationName);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'チームを削除できませんでした。');
      setIsDeleting(false);
    }
  };

  return (
    <>
      <section className="team-delete-panel">
        <div className="section-title-row">
          <span className="section-icon danger-icon">
            <Trash2 size={20} />
          </span>
          <div>
            <h2>チームの削除</h2>
          </div>
        </div>
        <p className="subtle-copy">チームと関連データにアクセスできなくなります。</p>
        <button className="danger-button" type="button" onClick={openConfirmation}>
          <Trash2 size={18} />
          チームを削除
        </button>
      </section>

      {isOpen && (
        <div className="modal-backdrop" onMouseDown={handleBackdropClick}>
          <section
            className="confirmation-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="team-delete-dialog-title"
          >
            <div className="confirmation-dialog-header">
              <div>
                <h2 id="team-delete-dialog-title">本当に削除しますか？</h2>
              </div>
              <button
                className="icon-button"
                type="button"
                onClick={closeConfirmation}
                disabled={isDeleting}
                aria-label="閉じる"
              >
                <X size={20} />
              </button>
            </div>

            <p className="confirmation-warning">
              この操作は取り消せません。確認のため、チーム名
              <strong>「{teamName}」</strong>を入力してください。
            </p>

            <form className="confirmation-form" onSubmit={handleSubmit}>
              <label className="field-label" htmlFor="team-delete-confirmation">
                チーム名
              </label>
              <input
                id="team-delete-confirmation"
                value={confirmationName}
                onChange={(event) => setConfirmationName(event.target.value)}
                autoComplete="off"
                disabled={isDeleting}
                autoFocus
              />
              {error && <p className="error-text">{error}</p>}
              <div className="form-actions">
                <button
                  className="secondary-button"
                  type="button"
                  onClick={closeConfirmation}
                  disabled={isDeleting}
                >
                  キャンセル
                </button>
                <button className="danger-button" type="submit" disabled={!canDelete}>
                  {isDeleting ? <Loader2 className="spin" size={18} /> : <Trash2 size={18} />}
                  {isDeleting ? '削除中' : '削除'}
                </button>
              </div>
            </form>
          </section>
        </div>
      )}
    </>
  );
}
