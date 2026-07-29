import type { User } from 'firebase/auth';
import {
  ArrowLeft,
  CalendarDays,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  CircleUserRound,
  ClipboardList,
  Clock3,
  FileText,
  Hash,
  Loader2,
  LogIn,
  LogOut,
  Map,
  MessageSquareText,
  PlayCircle,
  PlugZap,
  Plus,
  RotateCw,
  Save,
  Send,
  ShieldCheck,
  Sparkles,
  Trash2,
  UserCheck,
  Users,
  Video,
} from 'lucide-react';
import type { FormEvent } from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  createTeamTask,
  createRoadmapStep,
  deleteRoadmapStep,
  deleteTask,
  fetchTask,
  fetchMinutesTasks,
  fetchTeamMembers,
  fetchTeamTasks,
  generateTeamTasks,
  generateTaskRoadmap,
  reorderRoadmapSteps,
  updateRoadmapStep,
  updateTask,
  type RoadmapStep,
  type TaskRoadmap,
  type TaskCreateInput,
  type TaskStatus,
  type TaskUpdateInput,
  type TeamMemberSummary,
  type TeamTaskSummary,
} from './api/tasks';
import {
  createTeamMeeting,
  fetchMe,
  fetchMeetingDetail,
  fetchMeetingMinutes,
  fetchTeamMeetings,
  type MeetingStatus,
  type MeetingSummary,
  type TeamRole,
  type UserTeamSummary,
} from './api/workspace';
import {
  fetchSlackConnection,
  fetchSlackChannels,
  postMinutesToSlack,
  startSlackOAuth,
  updateSlackDefaultChannel,
  type SlackChannel,
  type SlackConnectionStatus,
} from './api/slack';
import {
  acceptTeamInvite,
  createTeam,
  fetchInvitePreview,
  type InvitePreview,
} from './api/teams';
import { TeamInvitePanel } from './components/TeamInvitePanel';
import { AuthProvider, getReadableAuthError, useAuth } from './contexts/AuthContext';

const gitSha = import.meta.env.VITE_GIT_SHA ?? 'local';
const appEnv = import.meta.env.VITE_APP_ENV ?? 'local';

type MeetingFilter = 'all' | MeetingStatus;
type TaskFilter = 'all' | TaskStatus;
type TaskOwnershipFilter = 'all' | 'mine';
type TeamView = 'meetings' | 'tasks';
type WorkspaceRoute =
  | { kind: 'home' }
  | { kind: 'team'; teamId: string }
  | { kind: 'teamTasks'; teamId: string }
  | { kind: 'meeting'; teamId: string; meetingId: string }
  | { kind: 'task'; teamId: string; taskId: string }
  | { kind: 'invite'; token: string };
type SlackNotice = {
  type: 'success' | 'error';
  teamId: string | null;
  message: string;
} | null;

type TeamTask = TeamTaskSummary & {
  roadmap: TaskRoadmap;
};

const roleLabels: Record<TeamRole, string> = {
  owner: 'オーナー',
  admin: '管理者',
  member: 'メンバー',
};

const filterLabels: Record<MeetingFilter, string> = {
  all: 'すべて',
  active: '進行中',
  ended: '終了済み',
};

const taskStatusLabels: Record<TaskStatus, string> = {
  todo: '未着手',
  in_progress: '進行中',
  done: '完了',
};

const taskFilterLabels: Record<TaskFilter, string> = {
  all: 'すべて',
  todo: '未着手',
  in_progress: '進行中',
  done: '完了',
};

const taskOwnershipFilterLabels: Record<TaskOwnershipFilter, string> = {
  all: '全員のタスク',
  mine: '自分のタスク',
};

function decodeRouteSegment(value: string) {
  try {
    return decodeURIComponent(value);
  } catch {
    return null;
  }
}

function readWorkspaceRoute(pathname = window.location.pathname): WorkspaceRoute {
  const taskMatch = pathname.match(/^\/teams\/([^/]+)\/tasks\/([^/]+)\/?$/);
  if (taskMatch) {
    const teamId = decodeRouteSegment(taskMatch[1]);
    const taskId = decodeRouteSegment(taskMatch[2]);
    if (teamId && taskId) return { kind: 'task', teamId, taskId };
  }

  const meetingMatch = pathname.match(/^\/teams\/([^/]+)\/meetings\/([^/]+)\/?$/);
  if (meetingMatch) {
    const teamId = decodeRouteSegment(meetingMatch[1]);
    const meetingId = decodeRouteSegment(meetingMatch[2]);
    if (teamId && meetingId) return { kind: 'meeting', teamId, meetingId };
  }

  const tasksMatch = pathname.match(/^\/teams\/([^/]+)\/tasks\/?$/);
  if (tasksMatch) {
    const teamId = decodeRouteSegment(tasksMatch[1]);
    if (teamId) return { kind: 'teamTasks', teamId };
  }

  const teamMatch = pathname.match(/^\/teams\/([^/]+)\/?$/);
  if (teamMatch) {
    const teamId = decodeRouteSegment(teamMatch[1]);
    if (teamId) return { kind: 'team', teamId };
  }

  const inviteMatch = pathname.match(/^\/invite\/([^/]+)\/?$/);
  if (inviteMatch) {
    const token = decodeRouteSegment(inviteMatch[1]);
    if (token) return { kind: 'invite', token };
  }

  return { kind: 'home' };
}

function workspaceRoutePath(route: WorkspaceRoute) {
  switch (route.kind) {
    case 'team':
      return `/teams/${encodeURIComponent(route.teamId)}`;
    case 'teamTasks':
      return `/teams/${encodeURIComponent(route.teamId)}/tasks`;
    case 'meeting':
      return `/teams/${encodeURIComponent(route.teamId)}/meetings/${encodeURIComponent(
        route.meetingId,
      )}`;
    case 'task':
      return `/teams/${encodeURIComponent(route.teamId)}/tasks/${encodeURIComponent(route.taskId)}`;
    case 'invite':
      return `/invite/${encodeURIComponent(route.token)}`;
    default:
      return '/home';
  }
}

function getRouteTeamId(route: WorkspaceRoute) {
  return 'teamId' in route ? route.teamId : null;
}

function shortSha(value: string) {
  return value === 'local' ? value : value.slice(0, 7);
}

function getDisplayName(user: User) {
  return user.displayName || user.email?.split('@')[0] || 'User';
}

function readSlackRedirectNotice(): SlackNotice {
  if (typeof window === 'undefined') return null;

  const { pathname, search } = window.location;
  if (pathname !== '/slack/success' && pathname !== '/slack/error') {
    return null;
  }

  const params = new URLSearchParams(search);
  const teamId = params.get('team_id');
  const reason = params.get('reason');
  const isSuccess = pathname === '/slack/success';

  return {
    type: isSuccess ? 'success' : 'error',
    teamId,
    message: isSuccess
      ? 'Slack連携が完了しました。'
      : `Slack連携に失敗しました。${reason ? ` (${reason})` : ''}`,
  };
}

function readWorkspaceEntry() {
  const slackNotice = readSlackRedirectNotice();
  const route =
    slackNotice?.teamId
      ? ({ kind: 'team', teamId: slackNotice.teamId } as const)
      : readWorkspaceRoute();
  return { route, slackNotice };
}

function getAiboardSlackRedirectState() {
  if (typeof window === 'undefined') return null;

  const { pathname, search } = window.location;
  if (pathname !== '/slack/aiboard/success' && pathname !== '/slack/aiboard/error') {
    return null;
  }

  const params = new URLSearchParams(search);
  return {
    isSuccess: pathname === '/slack/aiboard/success',
    meetingId: params.get('meeting_id'),
    reason: params.get('reason'),
  };
}

function AiboardSlackResultScreen() {
  const result = getAiboardSlackRedirectState();
  const isSuccess = result?.isSuccess ?? false;

  return (
    <main className="auth-layout">
      <section className="login-hero" aria-labelledby="aiboard-slack-result-title">
        <div className="login-brand" aria-label="WithDev">
          <span className="brand-mark">
            <PlugZap size={24} />
          </span>
          <span>WithDev</span>
        </div>
        <p className="eyebrow">Aiboard Slack</p>
        <h1 id="aiboard-slack-result-title">
          {isSuccess ? 'Slack連携が完了しました' : 'Slack連携に失敗しました'}
        </h1>
        <p className="subtle-copy">
          {isSuccess
            ? 'Aiboard の画面に戻って、チャンネル一覧を再読み込みしてください。'
            : `Aiboard の画面に戻って、もう一度Slack連携を試してください。${
                result?.reason ? ` (${result.reason})` : ''
              }`}
        </p>
        {result?.meetingId && <p className="subtle-copy">{result.meetingId}</p>}
      </section>
    </main>
  );
}

function createPendingRoadmap(taskId: string): TaskRoadmap {
  return {
    id: `pending-${taskId}`,
    overview: '',
    generation_status: 'pending',
    generation_error: null,
    version: 1,
    has_source_updates: false,
    steps: [],
  };
}

function withRoadmap(task: TeamTaskSummary): TeamTask {
  return {
    ...task,
    roadmap: task.roadmap ?? createPendingRoadmap(task.id),
  };
}

function withRoadmaps(tasks: TeamTaskSummary[]) {
  return tasks.map(withRoadmap);
}

function formatDateTime(value: number | null) {
  if (!value) return '-';

  return new Intl.DateTimeFormat('ja-JP', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value));
}

function formatDate(value: number | null) {
  if (!value) return '期限なし';

  return new Intl.DateTimeFormat('ja-JP', {
    month: '2-digit',
    day: '2-digit',
  }).format(new Date(value));
}

function toDateInputValue(value: number | null) {
  if (!value) return '';

  const date = new Date(value);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function toDatePayload(value: string) {
  if (!value) return null;
  return new Date(`${value}T00:00:00`).toISOString();
}

function LoadingScreen() {
  return (
    <main className="auth-layout">
      <div className="loading-panel" aria-live="polite">
        <Loader2 className="spin" size={28} />
      </div>
    </main>
  );
}

function LoginScreen() {
  const { error, loginWithGoogle } = useAuth();

  return (
    <main className="auth-layout">
      <section className="login-hero" aria-labelledby="login-title">
        <div className="login-brand" aria-label="WithDev">
          <span className="brand-mark">
            <Sparkles size={24} />
          </span>
          <span>WithDev</span>
        </div>
        <h1 id="login-title">チームで始める</h1>
        <button className="primary-button" type="button" onClick={() => void loginWithGoogle()}>
          <LogIn size={20} />
          Googleでログイン
        </button>
        {error && <p className="error-text">{getReadableAuthError(error)}</p>}
      </section>
    </main>
  );
}

type UserAvatarProps = {
  user: User;
};

function UserAvatar({ user }: UserAvatarProps) {
  if (user.photoURL) {
    return <img className="avatar" src={user.photoURL} alt={getDisplayName(user)} />;
  }

  return (
    <span className="avatar fallback" aria-label={getDisplayName(user)}>
      <CircleUserRound size={24} />
    </span>
  );
}

type AccountMenuProps = {
  user: User;
  onLogout: () => void;
};

function AccountMenu({ user, onLogout }: AccountMenuProps) {
  return (
    <div className="account-area">
      <UserAvatar user={user} />
      <div className="account-copy">
        <strong>{getDisplayName(user)}</strong>
        <span>{user.email}</span>
      </div>
      <button className="icon-button" type="button" onClick={onLogout} aria-label="ログアウト">
        <LogOut size={20} />
      </button>
    </div>
  );
}

type TeamCardProps = {
  team: UserTeamSummary;
  onOpen: () => void;
};

function TeamCard({ team, onOpen }: TeamCardProps) {
  return (
    <button className="team-card" type="button" onClick={onOpen}>
      <span className="team-icon">
        <Users size={24} />
      </span>
      <span className="team-main">
        <strong>{team.name}</strong>
        <span>{team.team_id}</span>
      </span>
      <span className="team-meta">
        <span>{roleLabels[team.role]}</span>
        <span>{team.member_count} member</span>
      </span>
      <ChevronRight size={22} />
    </button>
  );
}

type TeamSelectionScreenProps = {
  user: User;
  teams: UserTeamSummary[];
  onSelectTeam: (teamId: string) => void;
  onCreateTeam: (name: string) => Promise<void>;
  onLogout: () => void;
};

function TeamSelectionScreen({ user, teams, onSelectTeam, onCreateTeam, onLogout }: TeamSelectionScreenProps) {
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [name, setName] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!name.trim() || isSubmitting) return;
    setError(null);
    setIsSubmitting(true);
    try {
      await onCreateTeam(name.trim());
      setName('');
      setShowCreateForm(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'チームを作成できませんでした。');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <main className="app-layout">
      <header className="app-header">
        <div>
          <p className="eyebrow">WithDev</p>
          <h1>チームを選択</h1>
        </div>
        <AccountMenu user={user} onLogout={onLogout} />
      </header>

      <section className="toolbar">
        <div>
          <h2>所属チーム</h2>
          <p>{teams.length} 件のチームがあります。</p>
        </div>
        <button className="primary-button" type="button" onClick={() => setShowCreateForm((value) => !value)}>
          <Plus size={18} />
          新しいチーム
        </button>
      </section>

      {showCreateForm && (
        <form className="create-form" onSubmit={handleSubmit}>
          <div className="form-header">
            <span className="form-icon"><Users size={20} /></span>
            <div><p className="eyebrow">Create team</p><h2>チームを作成</h2></div>
          </div>
          <label className="field-label" htmlFor="team-name">チーム名</label>
          <input id="team-name" value={name} maxLength={255} onChange={(event) => setName(event.target.value)} autoFocus />
          {error && <p className="error-text">{error}</p>}
          <div className="form-actions">
            <button className="secondary-button" type="button" onClick={() => setShowCreateForm(false)}>キャンセル</button>
            <button className="primary-button" type="submit" disabled={!name.trim() || isSubmitting}>
              {isSubmitting ? <Loader2 className="spin" size={18} /> : <Plus size={18} />}
              作成
            </button>
          </div>
        </form>
      )}

      <section className="team-list" aria-label="所属チーム">
        {teams.map((team) => (
          <TeamCard key={team.team_id} team={team} onOpen={() => onSelectTeam(team.team_id)} />
        ))}
      </section>
      <BuildFooter />
    </main>
  );
}

type MeetingCreateFormProps = {
  onCreate: (title: string, initialTheme: string) => Promise<string>;
  onCancel: () => void;
};

function MeetingCreateForm({ onCreate, onCancel }: MeetingCreateFormProps) {
  const [title, setTitle] = useState('');
  const [initialTheme, setInitialTheme] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [popupError, setPopupError] = useState<string | null>(null);
  const canSubmit = title.trim().length > 0 && !isSubmitting;

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canSubmit) return;

    setPopupError(null);
    const meetingTab = window.open('about:blank', '_blank');
    if (!meetingTab) {
      setPopupError(
        '新しいタブを開けませんでした。ブラウザでポップアップを許可してから、もう一度お試しください。',
      );
      return;
    }
    meetingTab.opener = null;

    setIsSubmitting(true);
    try {
      const launchUrl = await onCreate(title.trim(), initialTheme.trim());
      meetingTab.location.replace(launchUrl);
      setTitle('');
      setInitialTheme('');
    } catch {
      meetingTab.close();
      // Parent components show the user-facing error.
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <form className="create-form" onSubmit={handleSubmit}>
      <div className="form-header">
        <span className="form-icon">
          <Plus size={20} />
        </span>
        <div>
          <h2>新しいミーティング</h2>
          <p>テーマは開始後にも追加できます。</p>
        </div>
      </div>

      <label className="field-label" htmlFor="meeting-title">
        ミーティングタイトル
      </label>
      <input
        id="meeting-title"
        value={title}
        maxLength={80}
        onChange={(event) => setTitle(event.target.value)}
        placeholder="例: 新機能の設計相談"
        autoFocus
      />

      <label className="field-label" htmlFor="meeting-theme">
        最初のテーマ（任意）
      </label>
      <input
        id="meeting-theme"
        value={initialTheme}
        maxLength={120}
        onChange={(event) => setInitialTheme(event.target.value)}
        placeholder="例: ユーザーが最初に迷う点を洗い出す"
      />

      {popupError && <p className="error-text">{popupError}</p>}

      <div className="form-actions">
        <button className="secondary-button" type="button" onClick={onCancel}>
          キャンセル
        </button>
        <button className="primary-button" type="submit" disabled={!canSubmit}>
          {isSubmitting ? <Loader2 className="spin" size={18} /> : <Video size={18} />}
          {isSubmitting ? '作成中' : '開始する'}
        </button>
      </div>
    </form>
  );
}

type MeetingCardProps = {
  meeting: MeetingSummary;
  onOpen: () => void;
};

function MeetingCard({ meeting, onOpen }: MeetingCardProps) {
  const statusLabel = meeting.status === 'active' ? '進行中' : '終了済み';

  return (
    <button className="meeting-card" type="button" onClick={onOpen}>
      <div className="meeting-card-main">
        <div className="meeting-status-row">
          <span className={`status-badge ${meeting.status}`}>{statusLabel}</span>
          <span className="meeting-id">{meeting.id}</span>
        </div>
        <h2>{meeting.title}</h2>
        <p>{meeting.initial_theme}</p>
      </div>

      <div className="meeting-card-meta">
        <span>
          <CalendarDays size={16} />
          作成 {formatDateTime(meeting.created_at)}
        </span>
        <span>
          <Clock3 size={16} />
          更新 {formatDateTime(meeting.updated_at)}
        </span>
        <span>
          <Users size={16} />
          {meeting.participant_count} member
        </span>
      </div>
    </button>
  );
}

function sortTasks(tasks: TeamTask[]) {
  const statusOrder: Record<TaskStatus, number> = {
    in_progress: 0,
    todo: 1,
    done: 2,
  };

  return [...tasks].sort((a, b) => {
    const statusDiff = statusOrder[a.status] - statusOrder[b.status];
    if (statusDiff !== 0) return statusDiff;

    const aDueDate = a.due_at ?? Number.MAX_SAFE_INTEGER;
    const bDueDate = b.due_at ?? Number.MAX_SAFE_INTEGER;
    if (aDueDate !== bDueDate) return aDueDate - bDueDate;

    return b.updated_at - a.updated_at;
  });
}

function TaskStatusBadge({ status }: { status: TaskStatus }) {
  return <span className={`task-status-badge ${status}`}>{taskStatusLabels[status]}</span>;
}

type TaskCardProps = {
  task: TeamTask;
  compact?: boolean;
  onOpen: () => void;
};

function TaskCard({ task, compact = false, onOpen }: TaskCardProps) {
  return (
    <button className={compact ? 'task-card compact' : 'task-card'} type="button" onClick={onOpen}>
      <div className="task-card-header">
        <TaskStatusBadge status={task.status} />
        <span>{formatDate(task.due_at)}</span>
      </div>
      <h3>{task.title}</h3>
      <div className="task-card-meta">
        <span>{task.assignee_name ?? '未担当'}</span>
        {task.source_minutes_id && <span>{task.source_minutes_id}</span>}
      </div>
    </button>
  );
}

type TaskSidebarProps = {
  currentUserId: string | null;
  tasks: TeamTask[];
  isLoading: boolean;
  error: string | null;
  onOpenTasks: () => void;
  onOpenTask: (taskId: string) => void;
};

function TaskSidebar({
  currentUserId,
  tasks,
  isLoading,
  error,
  onOpenTasks,
  onOpenTask,
}: TaskSidebarProps) {
  const myTasks = useMemo(
    () =>
      currentUserId
        ? sortTasks(
            tasks.filter(
              (task) => task.assignee_user_id === currentUserId && task.status !== 'done',
            ),
          )
        : [],
    [currentUserId, tasks],
  );

  return (
    <aside className="task-sidebar" aria-label="チームタスク">
      <button className="task-nav-button" type="button" onClick={onOpenTasks}>
        <span className="task-nav-icon">
          <ClipboardList size={22} />
        </span>
        <span className="task-nav-copy">
          <strong>全体タスク</strong>
          <span>{tasks.length} 件のチームタスク</span>
        </span>
        <ChevronRight size={20} />
      </button>

      <section className="my-task-panel">
        <div className="section-title-row">
          <span className="section-icon">
            <UserCheck size={22} />
          </span>
          <div>
            <p className="eyebrow">My tasks</p>
            <h2>自分の担当タスク</h2>
          </div>
        </div>

        {error && <p className="error-text">{error}</p>}

        {isLoading ? (
          <div className="inline-loading">
            <Loader2 className="spin" size={18} />
            <span>タスクを読み込み中</span>
          </div>
        ) : myTasks.length > 0 ? (
          <div className="task-list compact">
            {myTasks.map((task) => (
              <TaskCard key={task.id} task={task} compact onOpen={() => onOpenTask(task.id)} />
            ))}
          </div>
        ) : (
          <p className="task-empty">担当タスクはありません。</p>
        )}
      </section>
    </aside>
  );
}

type SlackSettingsPanelProps = {
  user: User;
  team: UserTeamSummary;
};

function SlackSettingsPanel({ user, team }: SlackSettingsPanelProps) {
  const [connection, setConnection] = useState<SlackConnectionStatus | null>(null);
  const [channels, setChannels] = useState<SlackChannel[]>([]);
  const [selectedChannelId, setSelectedChannelId] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isStartingSlack, setIsStartingSlack] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const selectedChannel = channels.find((channel) => channel.id === selectedChannelId) ?? null;

  useEffect(() => {
    let isCancelled = false;

    setIsLoading(true);
    setError(null);
    setSuccess(null);
    setConnection(null);
    setChannels([]);
    setSelectedChannelId('');

    void fetchSlackConnection(user, team.team_id)
      .then(async (nextConnection) => {
        if (isCancelled) return;
        setConnection(nextConnection);

        if (!nextConnection.connected) {
          return;
        }

        const nextChannels = await fetchSlackChannels(user, team.team_id);
        if (isCancelled) return;
        setChannels(nextChannels);
        setSelectedChannelId(
          nextConnection.default_channel_id || nextChannels[0]?.id || '',
        );
      })
      .catch((err) => {
        if (!isCancelled) {
          setError(err instanceof Error ? err.message : 'Slack連携状態を取得できませんでした。');
        }
      })
      .finally(() => {
        if (!isCancelled) {
          setIsLoading(false);
        }
      });

    return () => {
      isCancelled = true;
    };
  }, [team.team_id, user]);

  const handleStartSlackOAuth = async () => {
    setError(null);
    setSuccess(null);
    setIsStartingSlack(true);
    try {
      const url = await startSlackOAuth(user, team.team_id);
      window.location.assign(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Slack連携を開始できませんでした。');
      setIsStartingSlack(false);
    }
  };

  const handleSaveDefaultChannel = async () => {
    if (!selectedChannelId || isSaving) return;

    setError(null);
    setSuccess(null);
    setIsSaving(true);
    try {
      const nextConnection = await updateSlackDefaultChannel(
        user,
        team.team_id,
        selectedChannelId,
        selectedChannel?.name ?? null,
      );
      setConnection(nextConnection);
      setSuccess('Slackの既定投稿先を保存しました。');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Slackの既定投稿先を保存できませんでした。');
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <section className="slack-settings-panel">
      <div className="section-title-row">
        <span className="section-icon">
          <PlugZap size={22} />
        </span>
        <div>
          <p className="eyebrow">Slack</p>
          <h2>Slack連携</h2>
        </div>
      </div>

      {isLoading ? (
        <div className="inline-loading">
          <Loader2 className="spin" size={18} />
          <span>Slack連携状態を確認中</span>
        </div>
      ) : connection?.connected ? (
        <div className="slack-settings-body">
          <div className="connection-status-row">
            <p className="subtle-copy">
              {connection.slack_team_name
                ? `${connection.slack_team_name} と連携済みです。`
                : 'Slackワークスペースと連携済みです。'}
            </p>
            <button
              className="secondary-button"
              type="button"
              onClick={() => void handleStartSlackOAuth()}
              disabled={isStartingSlack}
            >
              {isStartingSlack ? <Loader2 className="spin" size={18} /> : <PlugZap size={18} />}
              {isStartingSlack ? '移動中' : '再連携'}
            </button>
          </div>
          <label className="field-label" htmlFor="default-slack-channel">
            既定の投稿先チャンネル
          </label>
          <div className="slack-select-row">
            <span className="select-icon">
              <Hash size={18} />
            </span>
            <select
              id="default-slack-channel"
              value={selectedChannelId}
              onChange={(event) => setSelectedChannelId(event.target.value)}
              disabled={channels.length === 0}
            >
              {channels.map((channel) => (
                <option key={channel.id} value={channel.id}>
                  {channel.name}
                </option>
              ))}
            </select>
            <button
              className="secondary-button"
              type="button"
              onClick={() => void handleSaveDefaultChannel()}
              disabled={!selectedChannelId || isSaving}
            >
              {isSaving ? <Loader2 className="spin" size={18} /> : <Save size={18} />}
              保存
            </button>
          </div>
        </div>
      ) : (
        <div className="slack-empty">
          <p>Slack連携は未設定です。</p>
          <button
            className="secondary-button"
            type="button"
            onClick={() => void handleStartSlackOAuth()}
            disabled={isStartingSlack}
          >
            {isStartingSlack ? <Loader2 className="spin" size={18} /> : <PlugZap size={18} />}
            Slack連携
          </button>
        </div>
      )}

      {error && <p className="error-text">{error}</p>}
      {success && <p className="success-text">{success}</p>}
    </section>
  );
}

type MeetingListScreenProps = {
  user: User;
  team: UserTeamSummary;
  meetings: MeetingSummary[];
  tasks: TeamTask[];
  currentUserId: string | null;
  isLoadingMeetings: boolean;
  isLoadingTasks: boolean;
  error: string | null;
  taskError: string | null;
  slackNotice: SlackNotice;
  onBackToTeams: () => void;
  onCreateMeeting: (title: string, initialTheme: string) => Promise<string>;
  onOpenMeeting: (meetingId: string) => void;
  onOpenTasks: () => void;
  onOpenTask: (taskId: string) => void;
  onLogout: () => void;
};

function MeetingListScreen({
  user,
  team,
  meetings,
  tasks,
  currentUserId,
  isLoadingMeetings,
  isLoadingTasks,
  error,
  taskError,
  slackNotice,
  onBackToTeams,
  onCreateMeeting,
  onOpenMeeting,
  onOpenTasks,
  onOpenTask,
  onLogout,
}: MeetingListScreenProps) {
  const [filter, setFilter] = useState<MeetingFilter>('all');
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [isStartingSlack, setIsStartingSlack] = useState(false);
  const [slackActionError, setSlackActionError] = useState<string | null>(null);
  const visibleMeetings = useMemo(() => {
    return meetings
      .filter((meeting) => filter === 'all' || meeting.status === filter)
      .sort((a, b) => b.updated_at - a.updated_at);
  }, [filter, meetings]);

  const handleCreateMeeting = async (title: string, initialTheme: string) => {
    const launchUrl = await onCreateMeeting(title, initialTheme);
    setShowCreateForm(false);
    return launchUrl;
  };

  const handleStartSlackOAuth = async () => {
    setSlackActionError(null);
    setIsStartingSlack(true);
    try {
      const url = await startSlackOAuth(user, team.team_id);
      window.location.assign(url);
    } catch (err) {
      setSlackActionError(err instanceof Error ? err.message : 'Slack連携を開始できませんでした。');
      setIsStartingSlack(false);
    }
  };

  return (
    <main className="app-layout">
      <header className="app-header">
        <div>
          <button className="quiet-button" type="button" onClick={onBackToTeams}>
            <ArrowLeft size={18} />
            チーム一覧へ
          </button>
          <p className="eyebrow">Team</p>
          <h1>{team.name}</h1>
          <p className="subtle-copy">{team.team_id}</p>
        </div>
        <AccountMenu user={user} onLogout={onLogout} />
      </header>

      <div className="team-content">
        <div className="meeting-column">
          <section className="toolbar">
            <div>
              <h2>ミーティング一覧</h2>
              <p>{meetings.length} 件のミーティングがあります。</p>
            </div>
            <div className="toolbar-actions">
              <button
                className="secondary-button"
                type="button"
                onClick={() => void handleStartSlackOAuth()}
                disabled={isStartingSlack}
              >
                {isStartingSlack ? <Loader2 className="spin" size={18} /> : <PlugZap size={18} />}
                Slack連携
              </button>
              <button
                className="primary-button"
                type="button"
                onClick={() => setShowCreateForm((value) => !value)}
              >
                <Plus size={18} />
                ミーティングを開始
              </button>
            </div>
          </section>

          {slackNotice && (
            <p className={slackNotice.type === 'success' ? 'success-text' : 'error-text'}>
              {slackNotice.message}
            </p>
          )}
          {slackActionError && <p className="error-text">{slackActionError}</p>}

          {showCreateForm && (
            <MeetingCreateForm
              onCreate={handleCreateMeeting}
              onCancel={() => setShowCreateForm(false)}
            />
          )}

          <section className="filter-bar" aria-label="ミーティングの絞り込み">
            {(Object.keys(filterLabels) as MeetingFilter[]).map((key) => (
              <button
                key={key}
                className={filter === key ? 'filter-button active' : 'filter-button'}
                type="button"
                onClick={() => setFilter(key)}
              >
                {filterLabels[key]}
              </button>
            ))}
          </section>

          {error && <p className="error-text">{error}</p>}

          {isLoadingMeetings ? (
            <section className="empty-state">
              <Loader2 className="spin" size={42} />
              <h2>読み込み中</h2>
            </section>
          ) : visibleMeetings.length > 0 ? (
            <section className="meeting-list" aria-label="ミーティング一覧">
              {visibleMeetings.map((meeting) => (
                <MeetingCard
                  key={meeting.id}
                  meeting={meeting}
                  onOpen={() => onOpenMeeting(meeting.id)}
                />
              ))}
            </section>
          ) : (
            <section className="empty-state">
              <CalendarDays size={42} />
              <h2>ミーティングがありません</h2>
              <p>新しいミーティングを開始できます。</p>
            </section>
          )}
        </div>

        <aside className="team-side-panel">
          <TeamInvitePanel user={user} teamId={team.team_id} />
          <SlackSettingsPanel user={user} team={team} />
          <TaskSidebar
            currentUserId={currentUserId}
            tasks={tasks}
            isLoading={isLoadingTasks}
            error={taskError}
            onOpenTasks={onOpenTasks}
            onOpenTask={onOpenTask}
          />
        </aside>
      </div>

      <BuildFooter />
    </main>
  );
}

type TeamTaskScreenProps = {
  user: User;
  team: UserTeamSummary;
  tasks: TeamTask[];
  members: TeamMemberSummary[];
  currentUserId: string | null;
  isLoading: boolean;
  error: string | null;
  onBackToMeetings: () => void;
  onCreateTask: (input: TaskCreateInput) => Promise<void>;
  onOpenTask: (taskId: string) => void;
  onLogout: () => void;
};

type TaskCreateFormProps = {
  members: TeamMemberSummary[];
  onCreate: (input: TaskCreateInput) => Promise<void>;
  onCancel: () => void;
};

function TaskCreateForm({ members, onCreate, onCancel }: TaskCreateFormProps) {
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const [status, setStatus] = useState<TaskStatus>('todo');
  const [assigneeUserId, setAssigneeUserId] = useState('');
  const [dueAt, setDueAt] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canSubmit = title.trim().length > 0 && !isSubmitting;

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canSubmit) return;

    setError(null);
    setIsSubmitting(true);
    try {
      await onCreate({
        title: title.trim(),
        body: body.trim(),
        status,
        assignee_user_id: assigneeUserId || null,
        due_at: toDatePayload(dueAt),
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'タスクの作成に失敗しました。');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <form className="task-create-panel" onSubmit={handleSubmit}>
      <div className="form-header">
        <span className="form-icon">
          <ClipboardList size={20} />
        </span>
        <div>
          <h2>新しいタスク</h2>
          <p>チームで管理するタスクを手動で登録します。</p>
        </div>
      </div>

      <div className="task-edit-grid">
        <label className="task-field">
          <span>タイトル</span>
          <input
            value={title}
            maxLength={255}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="例: リリース手順を確認する"
            autoFocus
          />
        </label>

        <label className="task-field">
          <span>ステータス</span>
          <select
            value={status}
            onChange={(event) => setStatus(event.target.value as TaskStatus)}
          >
            {(Object.keys(taskStatusLabels) as TaskStatus[]).map((statusKey) => (
              <option key={statusKey} value={statusKey}>
                {taskStatusLabels[statusKey]}
              </option>
            ))}
          </select>
        </label>

        <label className="task-field">
          <span>期限</span>
          <input type="date" value={dueAt} onChange={(event) => setDueAt(event.target.value)} />
        </label>
      </div>

      <label className="task-field">
        <span>本文（任意）</span>
        <textarea
          value={body}
          onChange={(event) => setBody(event.target.value)}
          placeholder="タスクの目的や完了条件を入力してください。"
        />
      </label>

      <label className="task-field">
        <span>担当者</span>
        <select
          value={assigneeUserId}
          onChange={(event) => setAssigneeUserId(event.target.value)}
        >
          <option value="">未担当</option>
          {members.map((member) => (
            <option key={member.user_id} value={member.user_id}>
              {member.display_name || member.email}
            </option>
          ))}
        </select>
      </label>

      {error && <p className="error-text">{error}</p>}

      <div className="form-actions">
        <button
          className="secondary-button"
          type="button"
          onClick={onCancel}
          disabled={isSubmitting}
        >
          キャンセル
        </button>
        <button className="primary-button" type="submit" disabled={!canSubmit}>
          {isSubmitting ? <Loader2 className="spin" size={18} /> : <Plus size={18} />}
          {isSubmitting ? '作成中' : 'タスクを作成'}
        </button>
      </div>
    </form>
  );
}

function TeamTaskScreen({
  user,
  team,
  tasks,
  members,
  currentUserId,
  isLoading,
  error,
  onBackToMeetings,
  onCreateTask,
  onOpenTask,
  onLogout,
}: TeamTaskScreenProps) {
  const [statusFilter, setStatusFilter] = useState<TaskFilter>('all');
  const [ownershipFilter, setOwnershipFilter] = useState<TaskOwnershipFilter>('all');
  const [showCreateForm, setShowCreateForm] = useState(false);
  const visibleTasks = useMemo(() => {
    return sortTasks(
      tasks.filter((task) => {
        const matchesStatus = statusFilter === 'all' || task.status === statusFilter;
        const matchesOwnership =
          ownershipFilter === 'all' ||
          (Boolean(currentUserId) &&
            task.assignee_user_id === currentUserId &&
            task.status !== 'done');
        return matchesStatus && matchesOwnership;
      }),
    );
  }, [currentUserId, ownershipFilter, statusFilter, tasks]);

  const handleCreateTask = async (input: TaskCreateInput) => {
    await onCreateTask(input);
    setStatusFilter('all');
    setOwnershipFilter('all');
    setShowCreateForm(false);
  };

  return (
    <main className="app-layout">
      <header className="app-header">
        <div>
          <button className="quiet-button" type="button" onClick={onBackToMeetings}>
            <ArrowLeft size={18} />
            ミーティング一覧へ
          </button>
          <p className="eyebrow">Team tasks</p>
          <h1>全体タスク</h1>
          <p className="subtle-copy">{team.name}</p>
        </div>
        <AccountMenu user={user} onLogout={onLogout} />
      </header>

      <section className="toolbar">
        <div>
          <h2>チーム全体のタスク</h2>
          <p>
            {visibleTasks.length} 件を表示 / 全 {tasks.length} 件
          </p>
        </div>
        <button
          className="primary-button"
          type="button"
          onClick={() => setShowCreateForm((value) => !value)}
        >
          <Plus size={18} />
          新規タスク
        </button>
      </section>

      {showCreateForm && (
        <TaskCreateForm
          members={members}
          onCreate={handleCreateTask}
          onCancel={() => setShowCreateForm(false)}
        />
      )}

      <section className="task-filter-groups" aria-label="タスクの絞り込み">
        <div className="task-filter-group">
          <span>担当</span>
          <div className="filter-bar" role="group" aria-label="担当者で絞り込み">
            {(Object.keys(taskOwnershipFilterLabels) as TaskOwnershipFilter[]).map((key) => (
              <button
                key={key}
                className={ownershipFilter === key ? 'filter-button active' : 'filter-button'}
                type="button"
                onClick={() => setOwnershipFilter(key)}
                disabled={key === 'mine' && !currentUserId}
              >
                {taskOwnershipFilterLabels[key]}
              </button>
            ))}
          </div>
        </div>
        <div className="task-filter-group">
          <span>状態</span>
          <div className="filter-bar" role="group" aria-label="ステータスで絞り込み">
            {(Object.keys(taskFilterLabels) as TaskFilter[]).map((key) => (
              <button
                key={key}
                className={statusFilter === key ? 'filter-button active' : 'filter-button'}
                type="button"
                onClick={() => setStatusFilter(key)}
              >
                {taskFilterLabels[key]}
              </button>
            ))}
          </div>
        </div>
      </section>

      {error && <p className="error-text">{error}</p>}

      {isLoading ? (
        <section className="empty-state">
          <Loader2 className="spin" size={42} />
          <h2>読み込み中</h2>
        </section>
      ) : visibleTasks.length > 0 ? (
        <section className="task-list" aria-label="チーム全体のタスク一覧">
          {visibleTasks.map((task) => (
            <TaskCard key={task.id} task={task} onOpen={() => onOpenTask(task.id)} />
          ))}
        </section>
      ) : (
        <section className="empty-state">
          <ClipboardList size={42} />
          <h2>タスクがありません</h2>
          <p>条件に一致するチームタスクはありません。</p>
        </section>
      )}

      <BuildFooter />
    </main>
  );
}

type TaskDetailScreenProps = {
  user: User;
  team: UserTeamSummary;
  task: TeamTask;
  members: TeamMemberSummary[];
  onBack: () => void;
  onSaveTask: (taskId: string, input: TaskUpdateInput) => Promise<void>;
  onDeleteTask: (taskId: string) => Promise<void>;
  onGenerateRoadmap: (taskId: string, reopen?: boolean) => Promise<void>;
  onCreateRoadmapStep: (
    taskId: string,
    input: { title: string; description: string },
  ) => Promise<void>;
  onUpdateRoadmapStep: (
    taskId: string,
    stepId: string,
    input: {
      title?: string;
      description?: string;
      status?: TaskStatus;
      reopen_task?: boolean;
    },
  ) => Promise<void>;
  onDeleteRoadmapStep: (taskId: string, stepId: string) => Promise<void>;
  onReorderRoadmapSteps: (taskId: string, stepIds: string[]) => Promise<void>;
  onLogout: () => void;
};

function TaskDetailScreen({
  user,
  team,
  task,
  members,
  onBack,
  onSaveTask,
  onDeleteTask,
  onGenerateRoadmap,
  onCreateRoadmapStep,
  onUpdateRoadmapStep,
  onDeleteRoadmapStep,
  onReorderRoadmapSteps,
  onLogout,
}: TaskDetailScreenProps) {
  const completedSteps = task.roadmap.steps.filter((step) => step.status === 'done').length;
  const initialDraft = useMemo(
    () => ({
      title: task.title,
      body: task.body,
      status: task.status,
      assignee_user_id: task.assignee_user_id,
      assignee_name: task.assignee_name ?? '',
      due_at: toDateInputValue(task.due_at),
    }),
    [task],
  );
  const [draft, setDraft] = useState(initialDraft);
  const [memberQuery, setMemberQuery] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [roadmapAction, setRoadmapAction] = useState<string | null>(null);
  const [editingStepId, setEditingStepId] = useState<string | null>(null);
  const [stepDraft, setStepDraft] = useState({ title: '', description: '' });
  const [newStepDraft, setNewStepDraft] = useState({ title: '', description: '' });
  const isDirty = JSON.stringify(draft) !== JSON.stringify(initialDraft);
  const filteredMembers = useMemo(() => {
    const query = memberQuery.trim().toLowerCase();
    if (!query) return members;

    return members.filter((member) => {
      const name = member.display_name.toLowerCase();
      const email = member.email.toLowerCase();
      return name.includes(query) || email.includes(query);
    });
  }, [memberQuery, members]);

  useEffect(() => {
    setDraft(initialDraft);
    setMemberQuery('');
    setError(null);
  }, [initialDraft]);

  const runRoadmapAction = async (actionId: string, action: () => Promise<void>) => {
    setError(null);
    setRoadmapAction(actionId);
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'ロードマップの更新に失敗しました。');
    } finally {
      setRoadmapAction(null);
    }
  };

  const beginStepEdit = (step: RoadmapStep) => {
    setEditingStepId(step.id);
    setStepDraft({ title: step.title, description: step.description });
  };

  const handleCreateStep = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!newStepDraft.title.trim() || !newStepDraft.description.trim()) return;
    await runRoadmapAction('create-step', async () => {
      await onCreateRoadmapStep(task.id, {
        title: newStepDraft.title.trim(),
        description: newStepDraft.description.trim(),
      });
      setNewStepDraft({ title: '', description: '' });
    });
  };

  const handleSaveStep = async (stepId: string) => {
    if (!stepDraft.title.trim() || !stepDraft.description.trim()) return;
    await runRoadmapAction(`edit-${stepId}`, async () => {
      await onUpdateRoadmapStep(task.id, stepId, {
        title: stepDraft.title.trim(),
        description: stepDraft.description.trim(),
      });
      setEditingStepId(null);
    });
  };

  const handleStepStatus = async (step: RoadmapStep, status: TaskStatus) => {
    await runRoadmapAction(`status-${step.id}`, () =>
      onUpdateRoadmapStep(task.id, step.id, {
        status,
        reopen_task: task.status === 'done' && status !== 'done',
      }),
    );
  };

  const handleMoveStep = async (stepIndex: number, direction: -1 | 1) => {
    const nextIndex = stepIndex + direction;
    if (nextIndex < 0 || nextIndex >= task.roadmap.steps.length) return;
    const stepIds = task.roadmap.steps.map((step) => step.id);
    [stepIds[stepIndex], stepIds[nextIndex]] = [stepIds[nextIndex], stepIds[stepIndex]];
    await runRoadmapAction('reorder', () => onReorderRoadmapSteps(task.id, stepIds));
  };

  const canSave = isDirty && draft.title.trim().length > 0 && draft.body.trim().length > 0 && !isSaving;

  const handleSelectMember = (member: TeamMemberSummary | null) => {
    setDraft((currentDraft) => ({
      ...currentDraft,
      assignee_user_id: member?.user_id ?? null,
      assignee_name: member?.display_name ?? '',
    }));
  };

  const handleSave = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canSave) return;

    setError(null);
    setIsSaving(true);
    try {
      await onSaveTask(task.id, {
        title: draft.title.trim(),
        body: draft.body.trim(),
        status: draft.status,
        assignee_user_id: draft.assignee_user_id,
        assignee_name: draft.assignee_user_id ? draft.assignee_name.trim() || null : null,
        due_at: toDatePayload(draft.due_at),
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'タスクの更新に失敗しました。');
    } finally {
      setIsSaving(false);
    }
  };

  const handleDelete = async () => {
    if (isDeleting) return;
    const confirmed = window.confirm('このタスクを削除しますか？');
    if (!confirmed) return;

    setError(null);
    setIsDeleting(true);
    try {
      await onDeleteTask(task.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'タスクの削除に失敗しました。');
      setIsDeleting(false);
    }
  };

  return (
    <main className="app-layout">
      <header className="app-header">
        <div>
          <button className="quiet-button" type="button" onClick={onBack}>
            <ArrowLeft size={18} />
            タスク一覧へ
          </button>
          <p className="eyebrow">Task roadmap</p>
          <h1>{task.title}</h1>
          <p className="subtle-copy">{team.name}</p>
        </div>
        <AccountMenu user={user} onLogout={onLogout} />
      </header>

      <section className="task-detail">
        <div className="task-detail-header">
          <span className="roadmap-icon">
            <ClipboardList size={26} />
          </span>
          <div>
            <p className="eyebrow">Task</p>
            <h2>タスク内容</h2>
            <p>変更内容を確認してから保存できます。</p>
          </div>
        </div>

        <form className="task-edit-form" onSubmit={handleSave}>
          <div className="task-edit-grid">
            <label className={draft.title !== initialDraft.title ? 'task-field dirty' : 'task-field'}>
              <span>タイトル</span>
              <input
                value={draft.title}
                onChange={(event) =>
                  setDraft((currentDraft) => ({ ...currentDraft, title: event.target.value }))
                }
              />
            </label>

            <label className={draft.status !== initialDraft.status ? 'task-field dirty' : 'task-field'}>
              <span>ステータス</span>
              <select
                value={draft.status}
                onChange={(event) =>
                  setDraft((currentDraft) => ({
                    ...currentDraft,
                    status: event.target.value as TaskStatus,
                  }))
                }
              >
                {(Object.keys(taskStatusLabels) as TaskStatus[]).map((statusKey) => (
                  <option key={statusKey} value={statusKey}>
                    {taskStatusLabels[statusKey]}
                  </option>
                ))}
              </select>
            </label>

            <label className={draft.due_at !== initialDraft.due_at ? 'task-field dirty' : 'task-field'}>
              <span>期限</span>
              <input
                type="date"
                value={draft.due_at}
                onChange={(event) =>
                  setDraft((currentDraft) => ({ ...currentDraft, due_at: event.target.value }))
                }
              />
            </label>
          </div>

          <label className={draft.body !== initialDraft.body ? 'task-field dirty' : 'task-field'}>
            <span>本文</span>
            <textarea
              value={draft.body}
              onChange={(event) =>
                setDraft((currentDraft) => ({ ...currentDraft, body: event.target.value }))
              }
            />
          </label>

          <section
            className={
              draft.assignee_user_id !== initialDraft.assignee_user_id
                ? 'assignee-picker dirty'
                : 'assignee-picker'
            }
          >
            <div className="assignee-picker-header">
              <div>
                <span>担当者</span>
                <strong>{draft.assignee_name || '未担当'}</strong>
              </div>
              <input
                value={memberQuery}
                onChange={(event) => setMemberQuery(event.target.value)}
                placeholder="メンバー名またはメールで検索"
              />
            </div>
            <div className="assignee-options">
              <button
                className={!draft.assignee_user_id ? 'assignee-option active' : 'assignee-option'}
                type="button"
                onClick={() => handleSelectMember(null)}
              >
                <span>未担当</span>
                <small>担当者なし</small>
              </button>
              {filteredMembers.map((member) => (
                <button
                  className={
                    draft.assignee_user_id === member.user_id
                      ? 'assignee-option active'
                      : 'assignee-option'
                  }
                  key={member.user_id}
                  type="button"
                  onClick={() => handleSelectMember(member)}
                >
                  <span>{member.display_name || member.email}</span>
                  <small>{member.email}</small>
                </button>
              ))}
            </div>
          </section>

          {error && <p className="error-text">{error}</p>}

          <div className="task-detail-actions">
            <button
              className="danger-button"
              type="button"
              onClick={() => void handleDelete()}
              disabled={isDeleting}
            >
              {isDeleting ? <Loader2 className="spin" size={18} /> : <Trash2 size={18} />}
              {isDeleting ? '削除中' : 'タスクを削除'}
            </button>
            <div>
              {isDirty && <span className="dirty-indicator">未保存の変更があります</span>}
              <button className="primary-button" type="submit" disabled={!canSave}>
                {isSaving ? <Loader2 className="spin" size={18} /> : <Save size={18} />}
                {isSaving ? '保存中' : '変更を保存'}
              </button>
            </div>
          </div>
        </form>

        <div className="task-detail-grid">
          <div>
            <span>ステータス</span>
            <strong>
              <TaskStatusBadge status={task.status} />
            </strong>
          </div>
          <div>
            <span>担当者</span>
            <strong>{task.assignee_name ?? '未担当'}</strong>
          </div>
          <div>
            <span>期限</span>
            <strong>{formatDate(task.due_at)}</strong>
          </div>
          <div>
            <span>元議事録</span>
            <strong>{task.source_minutes_id ?? '-'}</strong>
          </div>
          <div>
            <span>作成日時</span>
            <strong>{formatDateTime(task.created_at)}</strong>
          </div>
          <div>
            <span>最終更新</span>
            <strong>{formatDateTime(task.updated_at)}</strong>
          </div>
        </div>

        <section className="roadmap-panel">
          <div className="roadmap-panel-header">
            <div className="section-title-row">
              <span className="section-icon">
                <Map size={22} />
              </span>
              <div>
                <p className="eyebrow">Roadmap</p>
                <h2>完了までの道筋</h2>
                {task.roadmap.overview && <p>{task.roadmap.overview}</p>}
              </div>
            </div>
            <span className="roadmap-progress">
              {completedSteps}/{task.roadmap.steps.length}
            </span>
          </div>

          {task.roadmap.has_source_updates && task.status === 'done' && (
            <div className="roadmap-notice">
              <div>
                <strong>完了後に関連情報が更新されました</strong>
                <p>タスクを再オープンして、最新情報からロードマップを再生成できます。</p>
              </div>
              <button
                className="secondary-button"
                disabled={roadmapAction !== null}
                type="button"
                onClick={() =>
                  void runRoadmapAction('regenerate', () =>
                    onGenerateRoadmap(task.id, true),
                  )
                }
              >
                <RotateCw size={16} />
                再オープンして再生成
              </button>
            </div>
          )}

          {(task.roadmap.generation_status === 'pending' ||
            task.roadmap.generation_status === 'generating') && (
            <div className="roadmap-generation-state">
              <Loader2 className="spin" size={22} />
              <div>
                <strong>AIがロードマップを生成しています</strong>
                <p>タスクは保存済みです。この画面を開いたまま待つことができます。</p>
              </div>
              <button
                className="quiet-button"
                disabled={roadmapAction !== null}
                type="button"
                onClick={() =>
                  void runRoadmapAction('restart-generation', () =>
                    onGenerateRoadmap(task.id),
                  )
                }
              >
                <RotateCw size={15} />
                生成をやり直す
              </button>
            </div>
          )}

          {task.roadmap.generation_status === 'failed' && (
            <div className="roadmap-generation-state error">
              <div>
                <strong>ロードマップを生成できませんでした</strong>
                <p>{task.roadmap.generation_error ?? '時間をおいて再試行してください。'}</p>
              </div>
              <button
                className="secondary-button"
                disabled={roadmapAction !== null}
                type="button"
                onClick={() =>
                  void runRoadmapAction('retry', () => onGenerateRoadmap(task.id))
                }
              >
                <RotateCw size={16} />
                再試行
              </button>
            </div>
          )}

          {task.roadmap.steps.length > 0 && (
            <div className="roadmap-step-list">
              {task.roadmap.steps.map((step, index) => (
                <article className={`roadmap-step ${step.status}`} key={step.id}>
                  <span className="roadmap-step-index">{index + 1}</span>
                  <div>
                    {editingStepId === step.id ? (
                      <div className="roadmap-step-editor">
                        <input
                          aria-label="ステップ名"
                          maxLength={255}
                          value={stepDraft.title}
                          onChange={(event) =>
                            setStepDraft((current) => ({
                              ...current,
                              title: event.target.value,
                            }))
                          }
                        />
                        <textarea
                          aria-label="ステップの説明"
                          rows={3}
                          value={stepDraft.description}
                          onChange={(event) =>
                            setStepDraft((current) => ({
                              ...current,
                              description: event.target.value,
                            }))
                          }
                        />
                        <div className="roadmap-step-actions">
                          <button
                            className="primary-button"
                            disabled={roadmapAction !== null}
                            type="button"
                            onClick={() => void handleSaveStep(step.id)}
                          >
                            <Save size={15} />
                            保存
                          </button>
                          <button
                            className="quiet-button"
                            type="button"
                            onClick={() => setEditingStepId(null)}
                          >
                            キャンセル
                          </button>
                        </div>
                      </div>
                    ) : (
                      <>
                        <div className="roadmap-step-title">
                          <h3>{step.title}</h3>
                          <select
                            aria-label={`${step.title}の状態`}
                            disabled={roadmapAction !== null}
                            value={step.status}
                            onChange={(event) =>
                              void handleStepStatus(step, event.target.value as TaskStatus)
                            }
                          >
                            <option value="todo">未着手</option>
                            <option value="in_progress">進行中</option>
                            <option value="done">完了</option>
                          </select>
                        </div>
                        <p>{step.description}</p>
                        <div className="roadmap-step-actions">
                          <button
                            className="quiet-button"
                            type="button"
                            onClick={() => beginStepEdit(step)}
                          >
                            編集
                          </button>
                          <button
                            aria-label="一つ上へ"
                            className="icon-button"
                            disabled={index === 0 || roadmapAction !== null}
                            type="button"
                            onClick={() => void handleMoveStep(index, -1)}
                          >
                            <ChevronUp size={16} />
                          </button>
                          <button
                            aria-label="一つ下へ"
                            className="icon-button"
                            disabled={
                              index === task.roadmap.steps.length - 1 ||
                              roadmapAction !== null
                            }
                            type="button"
                            onClick={() => void handleMoveStep(index, 1)}
                          >
                            <ChevronDown size={16} />
                          </button>
                          <button
                            className="danger-link"
                            disabled={roadmapAction !== null}
                            type="button"
                            onClick={() => {
                              if (!window.confirm('このステップを削除しますか？')) return;
                              void runRoadmapAction(`delete-${step.id}`, () =>
                                onDeleteRoadmapStep(task.id, step.id),
                              );
                            }}
                          >
                            <Trash2 size={15} />
                            削除
                          </button>
                        </div>
                      </>
                    )}
                  </div>
                </article>
              ))}
            </div>
          )}

          {task.roadmap.generation_status === 'ready' &&
            task.roadmap.steps.length === 0 && (
              <p className="subtle-copy">ロードマップにはまだステップがありません。</p>
            )}

          <form className="roadmap-add-step" onSubmit={handleCreateStep}>
            <div>
              <strong>ステップを追加</strong>
              <p>追加したステップは、AIによる再生成でも保持されます。</p>
            </div>
            <input
              aria-label="新しいステップ名"
              maxLength={255}
              placeholder="ステップ名"
              value={newStepDraft.title}
              onChange={(event) =>
                setNewStepDraft((current) => ({ ...current, title: event.target.value }))
              }
            />
            <textarea
              aria-label="新しいステップの説明"
              placeholder="完了条件が分かる説明"
              rows={3}
              value={newStepDraft.description}
              onChange={(event) =>
                setNewStepDraft((current) => ({
                  ...current,
                  description: event.target.value,
                }))
              }
            />
            <button
              className="secondary-button"
              disabled={
                roadmapAction !== null ||
                !newStepDraft.title.trim() ||
                !newStepDraft.description.trim()
              }
              type="submit"
            >
              <Plus size={16} />
              追加
            </button>
          </form>
        </section>
      </section>

      <BuildFooter />
    </main>
  );
}

type SlackPostPanelProps = {
  user: User;
  team: UserTeamSummary;
  meeting: MeetingSummary;
};

function SlackPostPanel({ user, team, meeting }: SlackPostPanelProps) {
  const [channels, setChannels] = useState<SlackChannel[]>([]);
  const [selectedChannelId, setSelectedChannelId] = useState('');
  const [isLoadingChannels, setIsLoadingChannels] = useState(false);
  const [isStartingSlack, setIsStartingSlack] = useState(false);
  const [isPosting, setIsPosting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const minutesId = meeting.minutes_id;

  useEffect(() => {
    let isCancelled = false;

    if (!minutesId) {
      setChannels([]);
      setSelectedChannelId('');
      return () => {
        isCancelled = true;
      };
    }

    setIsLoadingChannels(true);
    setError(null);
    setSuccess(null);

    void fetchSlackChannels(user, team.team_id)
      .then((nextChannels) => {
        if (isCancelled) return;
        setChannels(nextChannels);
        setSelectedChannelId((currentValue) => currentValue || nextChannels[0]?.id || '');
      })
      .catch((err) => {
        if (isCancelled) return;
        setChannels([]);
        setSelectedChannelId('');
        setError(err instanceof Error ? err.message : 'Slackチャンネル一覧の取得に失敗しました。');
      })
      .finally(() => {
        if (!isCancelled) {
          setIsLoadingChannels(false);
        }
      });

    return () => {
      isCancelled = true;
    };
  }, [minutesId, team.team_id, user]);

  const handleStartSlackOAuth = async () => {
    setError(null);
    setSuccess(null);
    setIsStartingSlack(true);
    try {
      const url = await startSlackOAuth(user, team.team_id);
      window.location.assign(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Slack連携を開始できませんでした。');
      setIsStartingSlack(false);
    }
  };

  const handlePost = async () => {
    if (!minutesId || !selectedChannelId || isPosting) return;

    setError(null);
    setSuccess(null);
    setIsPosting(true);
    try {
      const post = await postMinutesToSlack(user, minutesId, selectedChannelId);
      const channelName =
        post.channel_name || channels.find((channel) => channel.id === selectedChannelId)?.name;
      setSuccess(`${channelName ? `#${channelName}` : 'Slack'} にMarkdownファイルを送信しました。`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Slackへの投稿に失敗しました。');
    } finally {
      setIsPosting(false);
    }
  };

  return (
    <section className="slack-post-panel">
      <div className="section-title-row">
        <span className="section-icon">
          <Send size={22} />
        </span>
        <div>
          <p className="eyebrow">Slack</p>
          <h2>Slackへ送信</h2>
        </div>
      </div>

      {isLoadingChannels ? (
        <div className="inline-loading">
          <Loader2 className="spin" size={18} />
          <span>Slackチャンネルを読み込み中</span>
        </div>
      ) : channels.length > 0 ? (
        <div className="slack-post-controls">
          <label className="field-label" htmlFor="slack-channel">
            投稿先チャンネル
          </label>
          <div className="slack-select-row">
            <span className="select-icon">
              <Hash size={18} />
            </span>
            <select
              id="slack-channel"
              value={selectedChannelId}
              onChange={(event) => setSelectedChannelId(event.target.value)}
            >
              {channels.map((channel) => (
                <option key={channel.id} value={channel.id}>
                  {channel.name}
                </option>
              ))}
            </select>
            <button
              className="primary-button"
              type="button"
              onClick={() => void handlePost()}
              disabled={!selectedChannelId || isPosting}
            >
              {isPosting ? <Loader2 className="spin" size={18} /> : <Send size={18} />}
              {isPosting ? '送信中' : 'Markdownを送信'}
            </button>
          </div>
        </div>
      ) : (
        <div className="slack-empty">
          <p>Slack連携が必要です。</p>
          <button
            className="secondary-button"
            type="button"
            onClick={() => void handleStartSlackOAuth()}
            disabled={isStartingSlack}
          >
            {isStartingSlack ? <Loader2 className="spin" size={18} /> : <PlugZap size={18} />}
            Slack連携
          </button>
        </div>
      )}

      {error && <p className="error-text">{error}</p>}
      {success && <p className="success-text">{success}</p>}
    </section>
  );
}

type MeetingMinutesPanelProps = {
  user: User;
  team: UserTeamSummary;
  meeting: MeetingSummary;
  onGenerateTasks: (minutesId: string) => Promise<void>;
  onOpenTask: (taskId: string) => void;
};

type MeetingRelatedTasksProps = {
  user: User;
  teamId: string;
  minutesId: string;
  refreshVersion: number;
  onOpenTask: (taskId: string) => void;
};

function MeetingRelatedTasks({
  user,
  teamId,
  minutesId,
  refreshVersion,
  onOpenTask,
}: MeetingRelatedTasksProps) {
  const [relatedTasks, setRelatedTasks] = useState<TeamTask[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let isCancelled = false;
    setIsLoading(true);
    setError(null);

    void fetchMinutesTasks(user, teamId, minutesId)
      .then((nextTasks) => {
        if (!isCancelled) {
          setRelatedTasks(withRoadmaps(nextTasks));
        }
      })
      .catch((err) => {
        if (!isCancelled) {
          setRelatedTasks([]);
          setError(
            err instanceof Error ? err.message : '議事録の関連タスクを取得できませんでした。',
          );
        }
      })
      .finally(() => {
        if (!isCancelled) {
          setIsLoading(false);
        }
      });

    return () => {
      isCancelled = true;
    };
  }, [minutesId, refreshVersion, teamId, user]);

  return (
    <section className="minutes-task-panel">
      <div className="section-title-row">
        <span className="section-icon">
          <ClipboardList size={22} />
        </span>
        <div>
          <p className="eyebrow">Related tasks</p>
          <h2>この議事録から作成・更新されたタスク</h2>
        </div>
      </div>

      {isLoading ? (
        <div className="inline-loading">
          <Loader2 className="spin" size={18} />
          <span>関連タスクを読み込み中</span>
        </div>
      ) : error ? (
        <p className="error-text">{error}</p>
      ) : relatedTasks.length > 0 ? (
        <div className="task-list compact">
          {sortTasks(relatedTasks).map((task) => (
            <TaskCard
              key={task.id}
              task={task}
              compact
              onOpen={() => onOpenTask(task.id)}
            />
          ))}
        </div>
      ) : (
        <p className="task-empty">この議事録に関連するタスクはありません。</p>
      )}
    </section>
  );
}

function MeetingMinutesPanel({
  user,
  team,
  meeting,
  onGenerateTasks,
  onOpenTask,
}: MeetingMinutesPanelProps) {
  const [isGeneratingTasks, setIsGeneratingTasks] = useState(false);
  const [taskGenerationError, setTaskGenerationError] = useState<string | null>(null);
  const [taskRefreshVersion, setTaskRefreshVersion] = useState(0);

  const handleGenerateTasks = async () => {
    if (!meeting.minutes_id || isGeneratingTasks) return;

    setTaskGenerationError(null);
    setIsGeneratingTasks(true);
    try {
      await onGenerateTasks(meeting.minutes_id);
      setTaskRefreshVersion((version) => version + 1);
    } catch (err) {
      setTaskGenerationError(err instanceof Error ? err.message : 'タスク生成に失敗しました。');
    } finally {
      setIsGeneratingTasks(false);
    }
  };

  if (!meeting.minutes) {
    return (
      <section className="minutes-panel">
        <div className="section-title-row">
          <span className="section-icon">
            <FileText size={22} />
          </span>
          <div>
            <p className="eyebrow">Minutes</p>
            <h2>議事録</h2>
          </div>
        </div>
        <p className="minutes-body">議事録はまだありません。</p>
      </section>
    );
  }

  return (
    <section className="minutes-panel">
      <div className="minutes-panel-header">
        <div className="section-title-row">
          <span className="section-icon">
            <FileText size={22} />
          </span>
          <div>
            <p className="eyebrow">Minutes</p>
            <h2>議事録</h2>
          </div>
        </div>
        <button
          className="secondary-button"
          type="button"
          onClick={() => void handleGenerateTasks()}
          disabled={!meeting.minutes_id || isGeneratingTasks}
        >
          {isGeneratingTasks ? <Loader2 className="spin" size={18} /> : <ClipboardList size={18} />}
          {isGeneratingTasks ? '生成中' : 'タスクを生成'}
        </button>
      </div>

      <p className="minutes-body">{meeting.minutes}</p>
      {taskGenerationError && <p className="error-text">{taskGenerationError}</p>}
      {meeting.minutes_id && (
        <MeetingRelatedTasks
          user={user}
          teamId={team.team_id}
          minutesId={meeting.minutes_id}
          refreshVersion={taskRefreshVersion}
          onOpenTask={onOpenTask}
        />
      )}
      <SlackPostPanel user={user} team={team} meeting={meeting} />
    </section>
  );
}

type MeetingRoomScreenProps = {
  user: User;
  team: UserTeamSummary;
  meeting: MeetingSummary;
  onBackToList: () => void;
  onGenerateTasks: (minutesId: string) => Promise<void>;
  onOpenTask: (taskId: string) => void;
};

function MeetingRoomScreen({
  user,
  team,
  meeting,
  onBackToList,
  onGenerateTasks,
  onOpenTask,
}: MeetingRoomScreenProps) {
  const isActive = meeting.status === 'active';
  const [hasJoined, setHasJoined] = useState(false);
  const canJoinMeeting = Boolean(meeting.launch_url);

  const handleJoinMeeting = () => {
    if (!meeting.launch_url) return;
    const meetingTab = window.open(meeting.launch_url, '_blank');
    if (!meetingTab) return;

    meetingTab.opener = null;
    setHasJoined(true);
  };

  return (
    <main className="app-layout">
      <button className="quiet-button" type="button" onClick={onBackToList}>
        <ArrowLeft size={18} />
        ミーティング一覧へ
      </button>

      <section className="meeting-room">
        <div className="room-title-row">
          <div>
            <p className="eyebrow">{team.name}</p>
            <h1>{meeting.title}</h1>
            <p className="subtle-copy">{meeting.id}</p>
          </div>
          <span className={`status-badge ${meeting.status}`}>
            {isActive ? '進行中' : '終了済み'}
          </span>
        </div>

        <div className="theme-panel">
          <span className="theme-icon">
            <MessageSquareText size={24} />
          </span>
          <div>
            <p className="eyebrow">Theme</p>
            <h2>{meeting.initial_theme}</h2>
          </div>
        </div>

        <div className="room-grid">
          <div>
            <span>作成日時</span>
            <strong>{formatDateTime(meeting.created_at)}</strong>
          </div>
          <div>
            <span>最終更新</span>
            <strong>{formatDateTime(meeting.updated_at)}</strong>
          </div>
          <div>
            <span>参加者</span>
            <strong>{meeting.participant_count} member</strong>
          </div>
          <div>
            <span>終了日時</span>
            <strong>{formatDateTime(meeting.ended_at)}</strong>
          </div>
        </div>

        {isActive ? (
          <section className="join-panel">
            <div>
              <p className="eyebrow">Live meeting</p>
              <h2>進行中のミーティング</h2>
            </div>
            <button
              className="primary-button"
              type="button"
              onClick={handleJoinMeeting}
              disabled={hasJoined || !canJoinMeeting}
            >
              <PlayCircle size={18} />
              {hasJoined ? '参加中' : 'ミーティングに参加'}
            </button>
          </section>
        ) : (
          <MeetingMinutesPanel
            user={user}
            team={team}
            meeting={meeting}
            onGenerateTasks={onGenerateTasks}
            onOpenTask={onOpenTask}
          />
        )}

        {isActive && meeting.minutes && (
          <MeetingMinutesPanel
            user={user}
            team={team}
            meeting={meeting}
            onGenerateTasks={onGenerateTasks}
            onOpenTask={onOpenTask}
          />
        )}
      </section>
    </main>
  );
}

function BuildFooter() {
  return (
    <footer className="build-footer" aria-label="Build information">
      <span>
        <ShieldCheck size={16} />
        {appEnv}
      </span>
      <span>{shortSha(gitSha)}</span>
    </footer>
  );
}

type InviteAcceptanceScreenProps = {
  user: User;
  preview: InvitePreview | null;
  isLoading: boolean;
  error: string | null;
  isAccepting: boolean;
  onAccept: () => void;
  onCancel: () => void;
  onLogout: () => void;
};

function InviteAcceptanceScreen({ user, preview, isLoading, error, isAccepting, onAccept, onCancel, onLogout }: InviteAcceptanceScreenProps) {
  return (
    <main className="app-layout invite-page">
      <header className="app-header">
        <div><p className="eyebrow">WithDev</p><h1>チーム招待</h1></div>
        <AccountMenu user={user} onLogout={onLogout} />
      </header>
      <section className="invite-confirmation">
        {isLoading ? (
          <><Loader2 className="spin" size={36} /><h2>招待を確認しています</h2></>
        ) : error ? (
          <><h2>招待リンクを利用できません</h2><p className="error-text">{error}</p></>
        ) : preview ? (
          <>
            <span className="section-icon"><Users size={22} /></span>
            <p className="eyebrow">Join team</p>
            <h2>{preview.team_name} に参加</h2>
            <p>参加すると、このチームのミーティング・議事録・タスクを利用できます。</p>
            <p className="subtle-copy">有効期限 {new Intl.DateTimeFormat('ja-JP', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(preview.expires_at))}</p>
          </>
        ) : null}
        <div className="form-actions">
          <button className="secondary-button" type="button" onClick={onCancel}>チーム一覧へ</button>
          {preview && !error && (
            <button className="primary-button" type="button" onClick={onAccept} disabled={isAccepting}>
              {isAccepting ? <Loader2 className="spin" size={18} /> : <UserCheck size={18} />}
              参加する
            </button>
          )}
        </div>
      </section>
      <BuildFooter />
    </main>
  );
}
type WorkspaceAppProps = {
  currentUser: User;
};

function WorkspaceApp({ currentUser }: WorkspaceAppProps) {
  const { logout } = useAuth();
  const [initialEntry] = useState(readWorkspaceEntry);
  const [route, setRoute] = useState<WorkspaceRoute>(initialEntry.route);
  const [teams, setTeams] = useState<UserTeamSummary[]>([]);
  const [currentUserId, setCurrentUserId] = useState<string | null>(null);
  const [isInitializing, setIsInitializing] = useState(true);
  const [isLoadingMeetings, setIsLoadingMeetings] = useState(false);
  const [isLoadingTasks, setIsLoadingTasks] = useState(false);
  const [workspaceError, setWorkspaceError] = useState<string | null>(null);
  const [meetingError, setMeetingError] = useState<string | null>(null);
  const [taskError, setTaskError] = useState<string | null>(null);
  const [slackNotice] = useState<SlackNotice>(initialEntry.slackNotice);
  const [meetings, setMeetings] = useState<MeetingSummary[]>([]);
  const [tasks, setTasks] = useState<TeamTask[]>([]);
  const [teamMembers, setTeamMembers] = useState<TeamMemberSummary[]>([]);
  const [invitePreview, setInvitePreview] = useState<InvitePreview | null>(null);
  const [inviteError, setInviteError] = useState<string | null>(null);
  const [isLoadingInvite, setIsLoadingInvite] = useState(initialEntry.route.kind === 'invite');
  const [isAcceptingInvite, setIsAcceptingInvite] = useState(false);
  const selectedTeamId = getRouteTeamId(route);
  const selectedMeetingId = route.kind === 'meeting' ? route.meetingId : null;
  const selectedTaskId = route.kind === 'task' ? route.taskId : null;
  const selectedTeamView: TeamView =
    route.kind === 'teamTasks' || route.kind === 'task' ? 'tasks' : 'meetings';
  const inviteToken = route.kind === 'invite' ? route.token : null;

  const navigateTo = useCallback((nextRoute: WorkspaceRoute, replace = false) => {
    const nextPath = workspaceRoutePath(nextRoute);
    if (`${window.location.pathname}${window.location.search}` !== nextPath) {
      window.history[replace ? 'replaceState' : 'pushState']({}, '', nextPath);
    }
    setRoute(nextRoute);
  }, []);

  useEffect(() => {
    const handlePopState = () => {
      setRoute(readWorkspaceRoute());
    };
    window.addEventListener('popstate', handlePopState);
    return () => {
      window.removeEventListener('popstate', handlePopState);
    };
  }, []);

  useEffect(() => {
    const canonicalPath = workspaceRoutePath(route);
    if (`${window.location.pathname}${window.location.search}` !== canonicalPath) {
      window.history.replaceState({}, '', canonicalPath);
    }
  }, [route]);

  useEffect(() => {
    let isCancelled = false;

    setIsInitializing(true);
    setWorkspaceError(null);
    setMeetingError(null);
    setCurrentUserId(null);
    setMeetings([]);
    setTasks([]);
    setTeamMembers([]);

    void fetchMe(currentUser)
      .then((context) => {
        if (isCancelled) return;
        const nextTeams = context.teams;
        setCurrentUserId(context.user.id);
        setTeams(nextTeams);
        setRoute((currentRoute) => {
          const routeTeamId = getRouteTeamId(currentRoute);
          if (routeTeamId && !nextTeams.some((team) => team.team_id === routeTeamId)) {
            const homeRoute = { kind: 'home' } as const;
            window.history.replaceState({}, '', workspaceRoutePath(homeRoute));
            return homeRoute;
          }
          return currentRoute;
        });
      })
      .catch((err) => {
        if (isCancelled) return;
        setTeams([]);
        setCurrentUserId(null);
        setWorkspaceError(err instanceof Error ? err.message : 'ユーザー初期化に失敗しました。');
      })
      .finally(() => {
        if (!isCancelled) {
          setIsInitializing(false);
        }
      });

    return () => {
      isCancelled = true;
    };
  }, [currentUser]);

  useEffect(() => {
    let isCancelled = false;

    if (!selectedTeamId) {
      setMeetings([]);
      setTasks([]);
      setTeamMembers([]);
      return () => {
        isCancelled = true;
      };
    }

    setIsLoadingMeetings(true);
    setIsLoadingTasks(true);
    setMeetingError(null);
    setTaskError(null);

    void fetchTeamMeetings(currentUser, selectedTeamId)
      .then((nextMeetings) => {
        if (!isCancelled) {
          setMeetings((currentMeetings) =>
            nextMeetings.map((meeting) => {
              const currentMeeting = currentMeetings.find((current) => current.id === meeting.id);
              if (!currentMeeting) return meeting;
              return {
                ...meeting,
                minutes_id: currentMeeting.minutes_id ?? meeting.minutes_id,
                minutes: currentMeeting.minutes ?? meeting.minutes,
              };
            }),
          );
        }
      })
      .catch((err) => {
        if (!isCancelled) {
          setMeetings([]);
          setMeetingError(err instanceof Error ? err.message : 'ミーティング一覧の取得に失敗しました。');
        }
      })
      .finally(() => {
        if (!isCancelled) {
          setIsLoadingMeetings(false);
        }
      });

    void Promise.all([
      fetchTeamTasks(currentUser, selectedTeamId),
      fetchTeamMembers(currentUser, selectedTeamId),
    ])
      .then(([nextTasks, nextMembers]) => {
        if (!isCancelled) {
          setTasks(withRoadmaps(nextTasks));
          setTeamMembers(nextMembers);
        }
      })
      .catch((err) => {
        if (!isCancelled) {
          setTasks([]);
          setTeamMembers([]);
          setTaskError(err instanceof Error ? err.message : 'タスク一覧の取得に失敗しました。');
        }
      })
      .finally(() => {
        if (!isCancelled) {
          setIsLoadingTasks(false);
        }
      });

    return () => {
      isCancelled = true;
    };
  }, [currentUser, selectedTeamId]);

  useEffect(() => {
    let isCancelled = false;
    if (!selectedTeamId || !selectedMeetingId) {
      return () => {
        isCancelled = true;
      };
    }

    setMeetingError(null);
    void Promise.all([
      fetchMeetingDetail(currentUser, selectedMeetingId),
      fetchMeetingMinutes(currentUser, selectedMeetingId),
    ])
      .then(([meeting, minutes]) => {
        if (isCancelled) return;
        if (meeting.team_id !== selectedTeamId) {
          throw new Error('このチームのミーティングではありません。');
        }
        const nextMeeting = {
          ...meeting,
          minutes_id: minutes?.id ?? null,
          minutes: minutes?.body ?? null,
        };
        setMeetings((currentMeetings) => [
          nextMeeting,
          ...currentMeetings.filter((currentMeeting) => currentMeeting.id !== nextMeeting.id),
        ]);
      })
      .catch((err) => {
        if (!isCancelled) {
          setMeetingError(
            err instanceof Error ? err.message : 'ミーティング詳細の取得に失敗しました。',
          );
        }
      });

    return () => {
      isCancelled = true;
    };
  }, [currentUser, selectedMeetingId, selectedTeamId]);

  useEffect(() => {
    let cancelled = false;
    if (isInitializing || !inviteToken) return () => { cancelled = true; };
    setIsLoadingInvite(true);
    setInviteError(null);
    void fetchInvitePreview(currentUser, inviteToken)
      .then((preview) => {
        if (cancelled) return;
        if (preview.already_member) {
          navigateTo({ kind: 'team', teamId: preview.team_id }, true);
        } else {
          setInvitePreview(preview);
        }
      })
      .catch((err) => {
        if (!cancelled) setInviteError(err instanceof Error ? err.message : '招待リンクを確認できませんでした。');
      })
      .finally(() => {
        if (!cancelled) setIsLoadingInvite(false);
      });
    return () => { cancelled = true; };
  }, [currentUser, inviteToken, isInitializing, navigateTo]);
  const selectedTeam = teams.find((team) => team.team_id === selectedTeamId) ?? null;
  const selectedMeeting =
    meetings.find(
      (meeting) => meeting.id === selectedMeetingId && meeting.team_id === selectedTeamId,
    ) ?? null;
  const selectedTask =
    tasks.find((task) => task.id === selectedTaskId && task.team_id === selectedTeamId) ?? null;
  const teamMeetings = selectedTeam
    ? meetings.filter((meeting) => meeting.team_id === selectedTeam.team_id)
    : [];
  const teamTasks = selectedTeam
    ? tasks.filter((task) => task.team_id === selectedTeam.team_id)
    : [];

  useEffect(() => {
    if (!selectedTask) return;
    const isGenerating =
      selectedTask.roadmap.generation_status === 'pending' ||
      selectedTask.roadmap.generation_status === 'generating';
    if (!isGenerating) return;

    let isCancelled = false;
    const refreshRoadmap = async () => {
      try {
        const nextTask = selectedTask.roadmap.id.startsWith('pending-')
          ? await generateTaskRoadmap(currentUser, selectedTask.id)
          : await fetchTask(currentUser, selectedTask.id);
        if (!isCancelled) {
          setTasks((currentTasks) =>
            currentTasks.map((task) =>
              task.id === nextTask.id ? withRoadmap(nextTask) : task,
            ),
          );
        }
      } catch (err) {
        if (!isCancelled) {
          setTaskError(
            err instanceof Error ? err.message : 'ロードマップの状態を取得できませんでした。',
          );
        }
      }
    };

    void refreshRoadmap();
    const intervalId = window.setInterval(() => void refreshRoadmap(), 2000);
    return () => {
      isCancelled = true;
      window.clearInterval(intervalId);
    };
  }, [
    currentUser,
    selectedTask?.id,
    selectedTask?.roadmap.generation_status,
    selectedTask?.roadmap.id,
  ]);

  const handleCreateTeam = async (name: string) => {
    setWorkspaceError(null);
    const team = await createTeam(currentUser, name);
    setTeams((current) => [...current.filter((item) => item.team_id !== team.team_id), team]);
    navigateTo({ kind: 'team', teamId: team.team_id });
  };

  const handleAcceptInvite = async () => {
    if (!inviteToken) return;
    setIsAcceptingInvite(true);
    setInviteError(null);
    try {
      const team = await acceptTeamInvite(currentUser, inviteToken);
      setTeams((current) => [...current.filter((item) => item.team_id !== team.team_id), team]);
      setInvitePreview(null);
      navigateTo({ kind: 'team', teamId: team.team_id }, true);
    } catch (err) {
      setInviteError(err instanceof Error ? err.message : 'チームに参加できませんでした。');
    } finally {
      setIsAcceptingInvite(false);
    }
  };

  const handleCancelInvite = () => {
    setInvitePreview(null);
    setInviteError(null);
    navigateTo({ kind: 'home' }, true);
  };
  const handleCreateMeeting = async (title: string, initialTheme: string) => {
    if (!selectedTeam) {
      throw new Error('チームが選択されていません。');
    }

    setMeetingError(null);
    try {
      const launch = await createTeamMeeting(
        currentUser,
        selectedTeam.team_id,
        title,
        initialTheme,
      );
      setMeetings((currentMeetings) => [
        launch.meeting,
        ...currentMeetings.filter((meeting) => meeting.id !== launch.meeting.id),
      ]);
      return launch.launch_url;
    } catch (err) {
      setMeetingError(err instanceof Error ? err.message : 'ミーティング作成に失敗しました。');
      throw err;
    }
  };

  const handleOpenMeeting = (meetingId: string) => {
    if (!selectedTeam) return;
    setMeetingError(null);
    navigateTo({ kind: 'meeting', teamId: selectedTeam.team_id, meetingId });
  };

  const handleGenerateTasksFromMinutes = async (minutesId: string) => {
    if (!selectedTeam) return;

    setTaskError(null);
    const nextTasks = await generateTeamTasks(currentUser, selectedTeam.team_id, minutesId);
    setTasks(withRoadmaps(nextTasks));
  };

  const handleCreateTask = async (input: TaskCreateInput) => {
    if (!selectedTeam) return;

    setTaskError(null);
    const createdTask = await createTeamTask(currentUser, selectedTeam.team_id, input);
    setTasks((currentTasks) => [
      withRoadmap(createdTask),
      ...currentTasks.filter((task) => task.id !== createdTask.id),
    ]);
  };

  const handleSaveTask = async (taskId: string, input: TaskUpdateInput) => {
    setTaskError(null);
    const updatedTask = await updateTask(currentUser, taskId, input);
    setTasks((currentTasks) =>
      currentTasks.map((task) => (task.id === taskId ? withRoadmap(updatedTask) : task)),
    );
  };

  const replaceTask = (nextTask: TeamTaskSummary) => {
    setTasks((currentTasks) =>
      currentTasks.map((task) =>
        task.id === nextTask.id ? withRoadmap(nextTask) : task,
      ),
    );
  };

  const roadmapVersion = (taskId: string) =>
    tasks.find((task) => task.id === taskId)?.roadmap.version;

  const handleGenerateRoadmap = async (taskId: string, reopen = false) => {
    replaceTask(
      await generateTaskRoadmap(
        currentUser,
        taskId,
        reopen,
        roadmapVersion(taskId),
      ),
    );
  };

  const handleCreateRoadmapStep = async (
    taskId: string,
    input: { title: string; description: string },
  ) => {
    replaceTask(
      await createRoadmapStep(currentUser, taskId, input, roadmapVersion(taskId)),
    );
  };

  const handleUpdateRoadmapStep = async (
    taskId: string,
    stepId: string,
    input: {
      title?: string;
      description?: string;
      status?: TaskStatus;
      reopen_task?: boolean;
    },
  ) => {
    replaceTask(
      await updateRoadmapStep(
        currentUser,
        taskId,
        stepId,
        input,
        roadmapVersion(taskId),
      ),
    );
  };

  const handleDeleteRoadmapStep = async (taskId: string, stepId: string) => {
    replaceTask(
      await deleteRoadmapStep(
        currentUser,
        taskId,
        stepId,
        roadmapVersion(taskId),
      ),
    );
  };

  const handleReorderRoadmapSteps = async (taskId: string, stepIds: string[]) => {
    replaceTask(
      await reorderRoadmapSteps(
        currentUser,
        taskId,
        stepIds,
        roadmapVersion(taskId),
      ),
    );
  };

  const handleDeleteTask = async (taskId: string) => {
    setTaskError(null);
    await deleteTask(currentUser, taskId);
    setTasks((currentTasks) => currentTasks.filter((task) => task.id !== taskId));
    if (selectedTeam) {
      navigateTo({ kind: 'teamTasks', teamId: selectedTeam.team_id }, true);
    }
  };

  const handleLogout = () => {
    void logout();
  };

  if (isInitializing) {
    return <LoadingScreen />;
  }

  if (inviteToken) {
    return (
      <InviteAcceptanceScreen
        user={currentUser}
        preview={invitePreview}
        isLoading={isLoadingInvite}
        error={inviteError}
        isAccepting={isAcceptingInvite}
        onAccept={() => void handleAcceptInvite()}
        onCancel={handleCancelInvite}
        onLogout={handleLogout}
      />
    );
  }
  if (workspaceError && teams.length === 0) {
    return (
      <main className="app-layout">
        <header className="app-header">
          <div>
            <p className="eyebrow">WithDev</p>
            <h1>チームを選択</h1>
            <p className="error-text">{workspaceError}</p>
          </div>
          <AccountMenu user={currentUser} onLogout={handleLogout} />
        </header>
        <BuildFooter />
      </main>
    );
  }

  if (selectedTeam && selectedTask && selectedTask.team_id === selectedTeam.team_id) {
    return (
      <TaskDetailScreen
        user={currentUser}
        team={selectedTeam}
        task={selectedTask}
        members={teamMembers}
        onBack={() => navigateTo({ kind: 'teamTasks', teamId: selectedTeam.team_id })}
        onSaveTask={handleSaveTask}
        onDeleteTask={handleDeleteTask}
        onGenerateRoadmap={handleGenerateRoadmap}
        onCreateRoadmapStep={handleCreateRoadmapStep}
        onUpdateRoadmapStep={handleUpdateRoadmapStep}
        onDeleteRoadmapStep={handleDeleteRoadmapStep}
        onReorderRoadmapSteps={handleReorderRoadmapSteps}
        onLogout={handleLogout}
      />
    );
  }

  if (selectedTeam && selectedMeeting) {
    return (
      <MeetingRoomScreen
        user={currentUser}
        team={selectedTeam}
        meeting={selectedMeeting}
        onBackToList={() => navigateTo({ kind: 'team', teamId: selectedTeam.team_id })}
        onGenerateTasks={handleGenerateTasksFromMinutes}
        onOpenTask={(taskId) =>
          navigateTo({ kind: 'task', teamId: selectedTeam.team_id, taskId })
        }
      />
    );
  }

  if (selectedTeam && selectedTeamView === 'tasks') {
    return (
      <TeamTaskScreen
        user={currentUser}
        team={selectedTeam}
        tasks={teamTasks}
        members={teamMembers}
        currentUserId={currentUserId}
        isLoading={isLoadingTasks}
        error={taskError}
        onBackToMeetings={() => navigateTo({ kind: 'team', teamId: selectedTeam.team_id })}
        onCreateTask={handleCreateTask}
        onOpenTask={(taskId) =>
          navigateTo({ kind: 'task', teamId: selectedTeam.team_id, taskId })
        }
        onLogout={handleLogout}
      />
    );
  }

  if (selectedTeam) {
    return (
      <MeetingListScreen
        user={currentUser}
        team={selectedTeam}
        meetings={teamMeetings}
        tasks={teamTasks}
        currentUserId={currentUserId}
        isLoadingMeetings={isLoadingMeetings}
        isLoadingTasks={isLoadingTasks}
        error={meetingError}
        taskError={taskError}
        slackNotice={
          slackNotice &&
          (!slackNotice.teamId || slackNotice.teamId === selectedTeam.team_id)
            ? slackNotice
            : null
        }
        onBackToTeams={() => navigateTo({ kind: 'home' })}
        onCreateMeeting={handleCreateMeeting}
        onOpenMeeting={handleOpenMeeting}
        onOpenTasks={() => navigateTo({ kind: 'teamTasks', teamId: selectedTeam.team_id })}
        onOpenTask={(taskId) =>
          navigateTo({ kind: 'task', teamId: selectedTeam.team_id, taskId })
        }
        onLogout={handleLogout}
      />
    );
  }

  return (
    <TeamSelectionScreen
      user={currentUser}
      teams={teams}
      onSelectTeam={(teamId) => navigateTo({ kind: 'team', teamId })}
      onCreateTeam={handleCreateTeam}
      onLogout={handleLogout}
    />
  );
}

function WithDevApp() {
  const { currentUser, loading } = useAuth();

  if (loading) {
    return <LoadingScreen />;
  }

  if (!currentUser) {
    return <LoginScreen />;
  }

  return <WorkspaceApp currentUser={currentUser} />;
}

function App() {
  if (getAiboardSlackRedirectState()) {
    return <AiboardSlackResultScreen />;
  }

  return (
    <AuthProvider>
      <WithDevApp />
    </AuthProvider>
  );
}

export default App;
