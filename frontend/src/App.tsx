import type { User } from 'firebase/auth';
import {
  ArrowLeft,
  CalendarDays,
  CheckCircle2,
  ChevronRight,
  CircleUserRound,
  ClipboardList,
  Clock3,
  FileText,
  Loader2,
  LogIn,
  LogOut,
  Map,
  MessageSquareText,
  PlayCircle,
  Plus,
  ShieldCheck,
  Sparkles,
  UserCheck,
  Users,
  Video,
} from 'lucide-react';
import type { FormEvent } from 'react';
import { useEffect, useMemo, useState } from 'react';
import { generateMinutesFromText } from './api/minutes';
import { AuthProvider, getReadableAuthError, useAuth } from './contexts/AuthContext';

const gitSha = import.meta.env.VITE_GIT_SHA ?? 'local';
const appEnv = import.meta.env.VITE_APP_ENV ?? 'local';
const meetingsStoragePrefix = 'withdev.meetings.v1';
const tasksStoragePrefix = 'withdev.tasks.v1';
const maxMinutesSourceLength = 50_000;

type TeamRole = 'owner' | 'admin' | 'member';
type MeetingStatus = 'active' | 'ended';
type MeetingFilter = 'all' | MeetingStatus;
type TaskStatus = 'todo' | 'doing' | 'done';
type TaskFilter = 'all' | TaskStatus;
type TeamView = 'meetings' | 'tasks';

type UserTeamSummary = {
  team_id: string;
  name: string;
  role: TeamRole;
  member_count: number;
};

type MeetingSummary = {
  id: string;
  team_id: string;
  title: string;
  initial_theme: string;
  status: MeetingStatus;
  participant_count: number;
  created_at: number;
  updated_at: number;
  ended_at: number | null;
  minutes: string | null;
};

type TeamTask = {
  id: string;
  team_id: string;
  title: string;
  status: TaskStatus;
  assignee_uid: string | null;
  assignee_name: string | null;
  source_meeting_id: string | null;
  due_date: number | null;
  created_at: number;
  updated_at: number;
  roadmap: TaskRoadmap;
};

type RoadmapStep = {
  id: string;
  title: string;
  description: string;
  status: TaskStatus;
};

type TaskRoadmap = {
  overview: string;
  steps: RoadmapStep[];
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
  doing: '進行中',
  done: '完了',
};

const taskFilterLabels: Record<TaskFilter, string> = {
  all: 'すべて',
  todo: '未着手',
  doing: '進行中',
  done: '完了',
};

function shortSha(value: string) {
  return value === 'local' ? value : value.slice(0, 7);
}

function getDisplayName(user: User) {
  return user.displayName || user.email?.split('@')[0] || 'User';
}

function createDefaultTeam(user: User): UserTeamSummary {
  const displayName = getDisplayName(user);
  return {
    team_id: `default_${user.uid}`,
    name: `${displayName} のチーム`,
    role: 'owner',
    member_count: 1,
  };
}

function getMeetingsStorageKey(userId: string) {
  return `${meetingsStoragePrefix}.${userId}`;
}

function getTasksStorageKey(userId: string) {
  return `${tasksStoragePrefix}.${userId}`;
}

function createMeetingId() {
  return `meeting_${globalThis.crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2)}`;
}

function createPlaceholderMinutes(title: string, initialTheme: string): string {
  return `${title}では「${initialTheme}」について話し合いました。現時点ではバックエンドから議事録を取得していないため、この文章はフロントエンドだけで動作確認するための仮の議事録です。実際の実装では、ミーティング終了後に生成または保存された議事録本文をバックエンドから取得し、この領域にそのまま表示します。議論の流れ、参加者の発言、合意に至った背景、次に確認したい論点などをひとつの文章として読みやすく表示できることを確認するため、少し長めの本文にしています。`;
}

function createPlaceholderRoadmap(taskTitle: string): TaskRoadmap {
  return {
    overview: `「${taskTitle}」を進めるための仮ロードマップです。今はフロントエンドだけで表示確認するための内容ですが、将来的にはバックエンドからタスクごとのロードマップを取得して差し替えます。`,
    steps: [
      {
        id: 'step_understand',
        title: '目的を確認する',
        description: 'このタスクで達成したい状態と、完了と判断できる条件を整理します。',
        status: 'done',
      },
      {
        id: 'step_collect',
        title: '必要な情報を集める',
        description: '関連するミーティング、議事録、チーム内の前提を確認します。',
        status: 'doing',
      },
      {
        id: 'step_execute',
        title: '作業を実行する',
        description: '整理した方針に沿ってタスクを進め、必要に応じて途中経過を共有します。',
        status: 'todo',
      },
      {
        id: 'step_share',
        title: '結果を共有する',
        description: '完了した内容、残った論点、次に必要なアクションをチームに共有します。',
        status: 'todo',
      },
    ],
  };
}

function createSeedMeetings(teamId: string): MeetingSummary[] {
  const now = Date.now();

  return [
    {
      id: 'demo_product_sync',
      team_id: teamId,
      title: 'プロダクト方針ミーティング',
      initial_theme: '最初に決めることを整理する',
      status: 'active',
      participant_count: 1,
      created_at: now - 1000 * 60 * 45,
      updated_at: now - 1000 * 60 * 8,
      ended_at: null,
      minutes: null,
    },
    {
      id: 'demo_weekly_review',
      team_id: teamId,
      title: '週次ふりかえり',
      initial_theme: '今週の学びと次の一手',
      status: 'ended',
      participant_count: 2,
      created_at: now - 1000 * 60 * 60 * 26,
      updated_at: now - 1000 * 60 * 60 * 25,
      ended_at: now - 1000 * 60 * 60 * 25,
      minutes: createPlaceholderMinutes('週次ふりかえり', '今週の学びと次の一手'),
    },
  ];
}

function parseMinutesText(value: unknown): string | null {
  if (typeof value === 'string') return value;
  if (value && typeof value === 'object' && 'summary' in value) {
    const legacyMinutes = value as { summary?: unknown };
    return typeof legacyMinutes.summary === 'string' ? legacyMinutes.summary : null;
  }
  return null;
}

function parseMeetingSummary(value: unknown): MeetingSummary | null {
  if (!value || typeof value !== 'object') return null;

  const meeting = value as Partial<MeetingSummary>;
  if (
    typeof meeting.id !== 'string' ||
    typeof meeting.team_id !== 'string' ||
    typeof meeting.title !== 'string' ||
    typeof meeting.initial_theme !== 'string' ||
    (meeting.status !== 'active' && meeting.status !== 'ended') ||
    typeof meeting.participant_count !== 'number' ||
    typeof meeting.created_at !== 'number' ||
    typeof meeting.updated_at !== 'number' ||
    (typeof meeting.ended_at !== 'number' && meeting.ended_at !== null)
  ) {
    return null;
  }

  const minutes =
    meeting.status === 'ended'
      ? parseMinutesText(meeting.minutes) ?? createPlaceholderMinutes(meeting.title, meeting.initial_theme)
      : null;

  return {
    id: meeting.id,
    team_id: meeting.team_id,
    title: meeting.title,
    initial_theme: meeting.initial_theme,
    status: meeting.status,
    participant_count: meeting.participant_count,
    created_at: meeting.created_at,
    updated_at: meeting.updated_at,
    ended_at: meeting.ended_at,
    minutes,
  };
}

function loadMeetings(userId: string, teamId: string) {
  if (typeof window === 'undefined') return createSeedMeetings(teamId);

  try {
    const rawValue = window.localStorage.getItem(getMeetingsStorageKey(userId));
    if (!rawValue) return createSeedMeetings(teamId);

    const parsedValue: unknown = JSON.parse(rawValue);
    if (!Array.isArray(parsedValue)) return createSeedMeetings(teamId);

    const meetings = parsedValue
      .map(parseMeetingSummary)
      .filter((meeting): meeting is MeetingSummary => meeting !== null);
    return meetings.length > 0 ? meetings : createSeedMeetings(teamId);
  } catch {
    return createSeedMeetings(teamId);
  }
}

function saveMeetings(userId: string, meetings: MeetingSummary[]) {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(getMeetingsStorageKey(userId), JSON.stringify(meetings));
}

function parseRoadmapStep(value: unknown): RoadmapStep | null {
  if (!value || typeof value !== 'object') return null;

  const step = value as Partial<RoadmapStep>;
  if (
    typeof step.id !== 'string' ||
    typeof step.title !== 'string' ||
    typeof step.description !== 'string' ||
    (step.status !== 'todo' && step.status !== 'doing' && step.status !== 'done')
  ) {
    return null;
  }

  return {
    id: step.id,
    title: step.title,
    description: step.description,
    status: step.status,
  };
}

function parseTaskRoadmap(value: unknown, taskTitle: string): TaskRoadmap {
  if (!value || typeof value !== 'object') return createPlaceholderRoadmap(taskTitle);

  const roadmap = value as Partial<TaskRoadmap>;
  if (typeof roadmap.overview !== 'string' || !Array.isArray(roadmap.steps)) {
    return createPlaceholderRoadmap(taskTitle);
  }

  const steps = roadmap.steps
    .map(parseRoadmapStep)
    .filter((step): step is RoadmapStep => step !== null);

  return steps.length > 0
    ? { overview: roadmap.overview, steps }
    : createPlaceholderRoadmap(taskTitle);
}

function createSeedTasks(teamId: string, user: User): TeamTask[] {
  const now = Date.now();
  const displayName = getDisplayName(user);

  return [
    {
      id: 'demo_task_prepare_topics',
      team_id: teamId,
      title: '次回ミーティングで確認する論点を整理する',
      status: 'doing',
      assignee_uid: user.uid,
      assignee_name: displayName,
      source_meeting_id: 'demo_product_sync',
      due_date: now + 1000 * 60 * 60 * 24 * 2,
      created_at: now - 1000 * 60 * 60 * 3,
      updated_at: now - 1000 * 60 * 24,
      roadmap: createPlaceholderRoadmap('次回ミーティングで確認する論点を整理する'),
    },
    {
      id: 'demo_task_review_minutes',
      team_id: teamId,
      title: '週次ふりかえりの議事録を確認する',
      status: 'todo',
      assignee_uid: user.uid,
      assignee_name: displayName,
      source_meeting_id: 'demo_weekly_review',
      due_date: now + 1000 * 60 * 60 * 24 * 4,
      created_at: now - 1000 * 60 * 60 * 20,
      updated_at: now - 1000 * 60 * 60 * 20,
      roadmap: createPlaceholderRoadmap('週次ふりかえりの議事録を確認する'),
    },
    {
      id: 'demo_task_schedule_next',
      team_id: teamId,
      title: '次回ミーティング候補日を共有する',
      status: 'todo',
      assignee_uid: null,
      assignee_name: null,
      source_meeting_id: null,
      due_date: now + 1000 * 60 * 60 * 24 * 7,
      created_at: now - 1000 * 60 * 60 * 5,
      updated_at: now - 1000 * 60 * 60 * 5,
      roadmap: createPlaceholderRoadmap('次回ミーティング候補日を共有する'),
    },
    {
      id: 'demo_task_done_summary',
      team_id: teamId,
      title: '初期デプロイ結果をチームに共有する',
      status: 'done',
      assignee_uid: user.uid,
      assignee_name: displayName,
      source_meeting_id: null,
      due_date: now - 1000 * 60 * 60 * 24,
      created_at: now - 1000 * 60 * 60 * 30,
      updated_at: now - 1000 * 60 * 60 * 2,
      roadmap: createPlaceholderRoadmap('初期デプロイ結果をチームに共有する'),
    },
  ];
}

function parseTeamTask(value: unknown): TeamTask | null {
  if (!value || typeof value !== 'object') return null;

  const task = value as Partial<TeamTask>;
  if (
    typeof task.id !== 'string' ||
    typeof task.team_id !== 'string' ||
    typeof task.title !== 'string' ||
    (task.status !== 'todo' && task.status !== 'doing' && task.status !== 'done') ||
    (typeof task.assignee_uid !== 'string' && task.assignee_uid !== null) ||
    (typeof task.assignee_name !== 'string' && task.assignee_name !== null) ||
    (typeof task.source_meeting_id !== 'string' && task.source_meeting_id !== null) ||
    (typeof task.due_date !== 'number' && task.due_date !== null) ||
    typeof task.created_at !== 'number' ||
    typeof task.updated_at !== 'number'
  ) {
    return null;
  }

  return {
    id: task.id,
    team_id: task.team_id,
    title: task.title,
    status: task.status,
    assignee_uid: task.assignee_uid,
    assignee_name: task.assignee_name,
    source_meeting_id: task.source_meeting_id,
    due_date: task.due_date,
    created_at: task.created_at,
    updated_at: task.updated_at,
    roadmap: parseTaskRoadmap(task.roadmap, task.title),
  };
}

function loadTasks(user: User, teamId: string) {
  if (typeof window === 'undefined') return createSeedTasks(teamId, user);

  try {
    const rawValue = window.localStorage.getItem(getTasksStorageKey(user.uid));
    if (!rawValue) return createSeedTasks(teamId, user);

    const parsedValue: unknown = JSON.parse(rawValue);
    if (!Array.isArray(parsedValue)) return createSeedTasks(teamId, user);

    const tasks = parsedValue
      .map(parseTeamTask)
      .filter((task): task is TeamTask => task !== null);
    return tasks.length > 0 ? tasks : createSeedTasks(teamId, user);
  } catch {
    return createSeedTasks(teamId, user);
  }
}

function saveTasks(userId: string, tasks: TeamTask[]) {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(getTasksStorageKey(userId), JSON.stringify(tasks));
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
  onLogout: () => void;
};

function TeamSelectionScreen({ user, teams, onSelectTeam, onLogout }: TeamSelectionScreenProps) {
  return (
    <main className="app-layout">
      <header className="app-header">
        <div>
          <p className="eyebrow">WithDev</p>
          <h1>チームを選択</h1>
        </div>
        <AccountMenu user={user} onLogout={onLogout} />
      </header>

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
  onCreate: (title: string, initialTheme: string) => void;
  onCancel: () => void;
};

function MeetingCreateForm({ onCreate, onCancel }: MeetingCreateFormProps) {
  const [title, setTitle] = useState('');
  const [initialTheme, setInitialTheme] = useState('');
  const canSubmit = title.trim().length > 0 && initialTheme.trim().length > 0;

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canSubmit) return;

    onCreate(title.trim(), initialTheme.trim());
    setTitle('');
    setInitialTheme('');
  };

  return (
    <form className="create-form" onSubmit={handleSubmit}>
      <div className="form-header">
        <span className="form-icon">
          <Plus size={20} />
        </span>
        <div>
          <h2>新しいミーティング</h2>
          <p>最初のテーマを決めて開始します。</p>
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
        最初のテーマ
      </label>
      <input
        id="meeting-theme"
        value={initialTheme}
        maxLength={120}
        onChange={(event) => setInitialTheme(event.target.value)}
        placeholder="例: ユーザーが最初に迷う点を洗い出す"
      />

      <div className="form-actions">
        <button className="secondary-button" type="button" onClick={onCancel}>
          キャンセル
        </button>
        <button className="primary-button" type="submit" disabled={!canSubmit}>
          <Video size={18} />
          開始する
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
    doing: 0,
    todo: 1,
    done: 2,
  };

  return [...tasks].sort((a, b) => {
    const statusDiff = statusOrder[a.status] - statusOrder[b.status];
    if (statusDiff !== 0) return statusDiff;

    const aDueDate = a.due_date ?? Number.MAX_SAFE_INTEGER;
    const bDueDate = b.due_date ?? Number.MAX_SAFE_INTEGER;
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
        <span>{formatDate(task.due_date)}</span>
      </div>
      <h3>{task.title}</h3>
      <div className="task-card-meta">
        <span>{task.assignee_name ?? '未担当'}</span>
        {task.source_meeting_id && <span>{task.source_meeting_id}</span>}
      </div>
    </button>
  );
}

type TaskSidebarProps = {
  user: User;
  tasks: TeamTask[];
  onOpenTasks: () => void;
  onOpenTask: (taskId: string) => void;
};

function TaskSidebar({ user, tasks, onOpenTasks, onOpenTask }: TaskSidebarProps) {
  const myTasks = useMemo(
    () => sortTasks(tasks.filter((task) => task.assignee_uid === user.uid)),
    [tasks, user.uid],
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

        {myTasks.length > 0 ? (
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

type MeetingListScreenProps = {
  user: User;
  team: UserTeamSummary;
  meetings: MeetingSummary[];
  tasks: TeamTask[];
  onBackToTeams: () => void;
  onCreateMeeting: (title: string, initialTheme: string) => void;
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
  onBackToTeams,
  onCreateMeeting,
  onOpenMeeting,
  onOpenTasks,
  onOpenTask,
  onLogout,
}: MeetingListScreenProps) {
  const [filter, setFilter] = useState<MeetingFilter>('all');
  const [showCreateForm, setShowCreateForm] = useState(false);
  const visibleMeetings = useMemo(() => {
    return meetings
      .filter((meeting) => filter === 'all' || meeting.status === filter)
      .sort((a, b) => b.updated_at - a.updated_at);
  }, [filter, meetings]);

  const handleCreateMeeting = (title: string, initialTheme: string) => {
    onCreateMeeting(title, initialTheme);
    setShowCreateForm(false);
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
            <button
              className="primary-button"
              type="button"
              onClick={() => setShowCreateForm((value) => !value)}
            >
              <Plus size={18} />
              ミーティングを開始
            </button>
          </section>

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

          {visibleMeetings.length > 0 ? (
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

        <TaskSidebar user={user} tasks={tasks} onOpenTasks={onOpenTasks} onOpenTask={onOpenTask} />
      </div>

      <BuildFooter />
    </main>
  );
}

type TeamTaskScreenProps = {
  user: User;
  team: UserTeamSummary;
  tasks: TeamTask[];
  onBackToMeetings: () => void;
  onOpenTask: (taskId: string) => void;
  onLogout: () => void;
};

function TeamTaskScreen({ user, team, tasks, onBackToMeetings, onOpenTask, onLogout }: TeamTaskScreenProps) {
  const [filter, setFilter] = useState<TaskFilter>('all');
  const visibleTasks = useMemo(() => {
    return sortTasks(tasks.filter((task) => filter === 'all' || task.status === filter));
  }, [filter, tasks]);

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
          <p>{tasks.length} 件のタスクがあります。</p>
        </div>
      </section>

      <section className="filter-bar" aria-label="タスクの絞り込み">
        {(Object.keys(taskFilterLabels) as TaskFilter[]).map((key) => (
          <button
            key={key}
            className={filter === key ? 'filter-button active' : 'filter-button'}
            type="button"
            onClick={() => setFilter(key)}
          >
            {taskFilterLabels[key]}
          </button>
        ))}
      </section>

      {visibleTasks.length > 0 ? (
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
  onBack: () => void;
  onLogout: () => void;
};

function TaskDetailScreen({ user, team, task, onBack, onLogout }: TaskDetailScreenProps) {
  const completedSteps = task.roadmap.steps.filter((step) => step.status === 'done').length;

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
            <Map size={26} />
          </span>
          <div>
            <p className="eyebrow">Roadmap</p>
            <h2>完了までの道筋</h2>
            <p>{task.roadmap.overview}</p>
          </div>
        </div>

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
            <strong>{formatDate(task.due_date)}</strong>
          </div>
          <div>
            <span>元ミーティング</span>
            <strong>{task.source_meeting_id ?? '-'}</strong>
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
                <CheckCircle2 size={22} />
              </span>
              <div>
                <p className="eyebrow">Steps</p>
                <h2>進め方</h2>
              </div>
            </div>
            <span className="roadmap-progress">
              {completedSteps}/{task.roadmap.steps.length}
            </span>
          </div>

          <div className="roadmap-step-list">
            {task.roadmap.steps.map((step, index) => (
              <article className={`roadmap-step ${step.status}`} key={step.id}>
                <span className="roadmap-step-index">{index + 1}</span>
                <div>
                  <div className="roadmap-step-title">
                    <h3>{step.title}</h3>
                    <TaskStatusBadge status={step.status} />
                  </div>
                  <p>{step.description}</p>
                </div>
              </article>
            ))}
          </div>
        </section>
      </section>

      <BuildFooter />
    </main>
  );
}

type MinutesGeneratorPanelProps = {
  user: User;
  meeting: MeetingSummary;
  onGenerated: (meetingId: string, minutes: string) => void;
};

function MinutesGeneratorPanel({ user, meeting, onGenerated }: MinutesGeneratorPanelProps) {
  const [sourceText, setSourceText] = useState('');
  const [isGenerating, setIsGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const textLength = sourceText.length;
  const canGenerate = sourceText.trim().length > 0 && textLength <= maxMinutesSourceLength && !isGenerating;

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canGenerate) return;

    setError(null);
    setIsGenerating(true);
    try {
      const minutes = await generateMinutesFromText(user, sourceText.trim());
      onGenerated(meeting.id, minutes);
    } catch (err) {
      setError(err instanceof Error ? err.message : '議事録の生成に失敗しました。');
    } finally {
      setIsGenerating(false);
    }
  };

  return (
    <form className="minutes-generator" onSubmit={handleSubmit}>
      <div className="section-title-row">
        <span className="section-icon">
          <Sparkles size={22} />
        </span>
        <div>
          <p className="eyebrow">Generate minutes</p>
          <h2>議事録を生成</h2>
        </div>
      </div>

      <label className="field-label" htmlFor="minutes-source">
        文字起こし
      </label>
      <textarea
        id="minutes-source"
        value={sourceText}
        maxLength={maxMinutesSourceLength + 1}
        onChange={(event) => setSourceText(event.target.value)}
        placeholder="会議の文字起こしやメモを貼り付けてください。"
      />

      <div className="minutes-generator-footer">
        <span className={textLength > maxMinutesSourceLength ? 'count-text error' : 'count-text'}>
          {textLength}/{maxMinutesSourceLength}
        </span>
        <button className="primary-button" type="submit" disabled={!canGenerate}>
          {isGenerating ? <Loader2 className="spin" size={18} /> : <FileText size={18} />}
          {isGenerating ? '生成中' : '議事録を生成'}
        </button>
      </div>

      {error && <p className="error-text">{error}</p>}
    </form>
  );
}

function MeetingMinutesPanel({ minutes }: { minutes: string | null }) {
  if (!minutes) {
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
      <div className="section-title-row">
        <span className="section-icon">
          <FileText size={22} />
        </span>
        <div>
          <p className="eyebrow">Minutes</p>
          <h2>議事録</h2>
        </div>
      </div>

      <p className="minutes-body">{minutes}</p>
    </section>
  );
}

type MeetingRoomScreenProps = {
  user: User;
  team: UserTeamSummary;
  meeting: MeetingSummary;
  onBackToList: () => void;
  onEndMeeting: (meetingId: string) => void;
  onSaveMinutes: (meetingId: string, minutes: string) => void;
};

function MeetingRoomScreen({
  user,
  team,
  meeting,
  onBackToList,
  onEndMeeting,
  onSaveMinutes,
}: MeetingRoomScreenProps) {
  const isActive = meeting.status === 'active';
  const [hasJoined, setHasJoined] = useState(false);

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
          <>
            <section className="join-panel">
              <div>
                <p className="eyebrow">Live meeting</p>
                <h2>進行中のミーティング</h2>
              </div>
              <button
                className="primary-button"
                type="button"
                onClick={() => setHasJoined(true)}
                disabled={hasJoined}
              >
                <PlayCircle size={18} />
                {hasJoined ? '参加中' : 'ミーティングに参加'}
              </button>
            </section>

            <button className="danger-button" type="button" onClick={() => onEndMeeting(meeting.id)}>
              <CheckCircle2 size={18} />
              ミーティングを終了
            </button>
          </>
        ) : (
          <MeetingMinutesPanel minutes={meeting.minutes} />
        )}

        <MinutesGeneratorPanel user={user} meeting={meeting} onGenerated={onSaveMinutes} />
        {isActive && meeting.minutes && <MeetingMinutesPanel minutes={meeting.minutes} />}
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

type WorkspaceAppProps = {
  currentUser: User;
};

function WorkspaceApp({ currentUser }: WorkspaceAppProps) {
  const { logout } = useAuth();
  const teams = useMemo(() => [createDefaultTeam(currentUser)], [currentUser]);
  const defaultTeamId = teams[0].team_id;
  const [selectedTeamId, setSelectedTeamId] = useState<string | null>(null);
  const [selectedMeetingId, setSelectedMeetingId] = useState<string | null>(null);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [selectedTeamView, setSelectedTeamView] = useState<TeamView>('meetings');
  const [meetings, setMeetings] = useState<MeetingSummary[]>(() =>
    loadMeetings(currentUser.uid, defaultTeamId),
  );
  const [tasks, setTasks] = useState<TeamTask[]>(() => loadTasks(currentUser, defaultTeamId));

  useEffect(() => {
    setSelectedTeamId(null);
    setSelectedMeetingId(null);
    setSelectedTaskId(null);
    setSelectedTeamView('meetings');
    setMeetings(loadMeetings(currentUser.uid, defaultTeamId));
    setTasks(loadTasks(currentUser, defaultTeamId));
  }, [currentUser, currentUser.uid, defaultTeamId]);

  useEffect(() => {
    saveMeetings(currentUser.uid, meetings);
  }, [currentUser.uid, meetings]);

  useEffect(() => {
    saveTasks(currentUser.uid, tasks);
  }, [currentUser.uid, tasks]);

  const selectedTeam = teams.find((team) => team.team_id === selectedTeamId) ?? null;
  const selectedMeeting = meetings.find((meeting) => meeting.id === selectedMeetingId) ?? null;
  const selectedTask = tasks.find((task) => task.id === selectedTaskId) ?? null;
  const teamMeetings = selectedTeam
    ? meetings.filter((meeting) => meeting.team_id === selectedTeam.team_id)
    : [];
  const teamTasks = selectedTeam
    ? tasks.filter((task) => task.team_id === selectedTeam.team_id)
    : [];

  const handleCreateMeeting = (title: string, initialTheme: string) => {
    if (!selectedTeam) return;

    const now = Date.now();
    const nextMeeting: MeetingSummary = {
      id: createMeetingId(),
      team_id: selectedTeam.team_id,
      title,
      initial_theme: initialTheme,
      status: 'active',
      participant_count: 1,
      created_at: now,
      updated_at: now,
      ended_at: null,
      minutes: null,
    };

    setMeetings((currentMeetings) => [nextMeeting, ...currentMeetings]);
    setSelectedMeetingId(nextMeeting.id);
  };

  const handleEndMeeting = (meetingId: string) => {
    const now = Date.now();
    setMeetings((currentMeetings) =>
      currentMeetings.map((meeting) =>
        meeting.id === meetingId
          ? {
              ...meeting,
              status: 'ended',
              updated_at: now,
              ended_at: now,
              minutes: meeting.minutes ?? createPlaceholderMinutes(meeting.title, meeting.initial_theme),
            }
          : meeting,
      ),
    );
  };

  const handleSaveMinutes = (meetingId: string, minutes: string) => {
    const now = Date.now();
    setMeetings((currentMeetings) =>
      currentMeetings.map((meeting) =>
        meeting.id === meetingId
          ? {
              ...meeting,
              minutes,
              updated_at: now,
            }
          : meeting,
      ),
    );
  };

  const handleLogout = () => {
    void logout();
  };

  if (selectedTeam && selectedTask && selectedTask.team_id === selectedTeam.team_id) {
    return (
      <TaskDetailScreen
        user={currentUser}
        team={selectedTeam}
        task={selectedTask}
        onBack={() => setSelectedTaskId(null)}
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
        onBackToList={() => setSelectedMeetingId(null)}
        onEndMeeting={handleEndMeeting}
        onSaveMinutes={handleSaveMinutes}
      />
    );
  }

  if (selectedTeam && selectedTeamView === 'tasks') {
    return (
      <TeamTaskScreen
        user={currentUser}
        team={selectedTeam}
        tasks={teamTasks}
        onBackToMeetings={() => setSelectedTeamView('meetings')}
        onOpenTask={(taskId) => {
          setSelectedMeetingId(null);
          setSelectedTaskId(taskId);
        }}
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
        onBackToTeams={() => {
          setSelectedTeamId(null);
          setSelectedMeetingId(null);
          setSelectedTaskId(null);
          setSelectedTeamView('meetings');
        }}
        onCreateMeeting={handleCreateMeeting}
        onOpenMeeting={(meetingId) => {
          setSelectedTaskId(null);
          setSelectedMeetingId(meetingId);
        }}
        onOpenTasks={() => setSelectedTeamView('tasks')}
        onOpenTask={(taskId) => {
          setSelectedMeetingId(null);
          setSelectedTaskId(taskId);
        }}
        onLogout={handleLogout}
      />
    );
  }

  return (
    <TeamSelectionScreen
      user={currentUser}
      teams={teams}
      onSelectTeam={(teamId) => {
        setSelectedTeamId(teamId);
        setSelectedTeamView('meetings');
      }}
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
  return (
    <AuthProvider>
      <WithDevApp />
    </AuthProvider>
  );
}

export default App;
