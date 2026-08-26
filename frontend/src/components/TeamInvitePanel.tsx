import type { User } from 'firebase/auth';
import { Check, ClipboardCopy, Link2, Loader2, Trash2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import {
  createTeamInvite,
  fetchTeamInvites,
  revokeTeamInvite,
  type TeamInvite,
} from '../api/teams';

const statusLabels: Record<TeamInvite['status'], string> = {
  active: '有効',
  expired: '期限切れ',
  revoked: '無効',
};

type TeamInvitePanelProps = {
  user: User;
  teamId: string;
};

export function TeamInvitePanel({ user, teamId }: TeamInvitePanelProps) {
  const [invites, setInvites] = useState<TeamInvite[]>([]);
  const [createdUrl, setCreatedUrl] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isCreating, setIsCreating] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const visibleInvites = invites.filter((invite) => invite.status !== 'expired');

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    void fetchTeamInvites(user, teamId)
      .then((items) => {
        if (!cancelled) setInvites(items);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : '招待一覧を取得できませんでした。');
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [teamId, user]);

  const handleCreate = async () => {
    setError(null);
    setCopied(false);
    setIsCreating(true);
    try {
      const result = await createTeamInvite(user, teamId);
      setCreatedUrl(result.inviteUrl);
      setInvites((current) => [result.invite, ...current]);
    } catch (err) {
      setError(err instanceof Error ? err.message : '招待リンクを作成できませんでした。');
    } finally {
      setIsCreating(false);
    }
  };

  const handleCopy = async () => {
    if (!createdUrl) return;
    await navigator.clipboard.writeText(createdUrl);
    setCopied(true);
  };

  const handleRevoke = async (inviteId: string) => {
    setError(null);
    try {
      await revokeTeamInvite(user, teamId, inviteId);
      setInvites((current) =>
        current.map((invite) =>
          invite.id === inviteId ? { ...invite, status: 'revoked', can_revoke: false } : invite,
        ),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : '招待リンクを無効化できませんでした。');
    }
  };

  return (
    <section className="invite-panel">
      <div className="invite-panel-header">
        <span className="section-icon"><Link2 size={20} /></span>
        <div>
          <h2>招待リンク</h2>
        </div>
      </div>

      <button className="secondary-button" type="button" onClick={() => void handleCreate()} disabled={isCreating}>
        {isCreating ? <Loader2 className="spin" size={18} /> : <Link2 size={18} />}
        招待リンクを作成
      </button>

      {createdUrl && (
        <div className="invite-created">
          <p>このリンクは今だけコピーできます。</p>
          <div className="invite-url-row">
            <input value={createdUrl} readOnly aria-label="作成した招待リンク" />
            <button className="icon-button" type="button" onClick={() => void handleCopy()} title="コピー">
              {copied ? <Check size={18} /> : <ClipboardCopy size={18} />}
            </button>
          </div>
        </div>
      )}

      {error && <p className="error-text">{error}</p>}
      {isLoading ? (
        <Loader2 className="spin" size={24} />
      ) : visibleInvites.length === 0 ? (
        <p className="task-empty">作成済みの招待リンクはありません。</p>
      ) : (
        <div className="invite-list">
          {visibleInvites.map((invite) => (
            <article className="invite-item" key={invite.id}>
              <div className="invite-item-row">
                <span className={`invite-status ${invite.status}`}>{statusLabels[invite.status]}</span>
                {invite.can_revoke && invite.status === 'active' && (
                  <button className="icon-button small" type="button" onClick={() => void handleRevoke(invite.id)} title="無効化">
                    <Trash2 size={16} />
                  </button>
                )}
              </div>
              <strong>{invite.created_by.name}</strong>
              <span>作成 {formatDate(invite.created_at)}</span>
              <span>期限 {formatDate(invite.expires_at)}</span>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat('ja-JP', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(new Date(value));
}
