import type { User } from 'firebase/auth';
import {
  ArrowLeft,
  CalendarDays,
  CheckCircle2,
  ChevronRight,
  CircleUserRound,
  Clock3,
  FileText,
  Loader2,
  LogIn,
  LogOut,
  MessageSquareText,
  PlayCircle,
  Plus,
  ShieldCheck,
  Sparkles,
  Users,
  Video,
} from 'lucide-react';
import type { FormEvent } from 'react';
import { useEffect, useMemo, useState } from 'react';
import { AuthProvider, getReadableAuthError, useAuth } from './contexts/AuthContext';

const gitSha = import.meta.env.VITE_GIT_SHA ?? 'local';
const appEnv = import.meta.env.VITE_APP_ENV ?? 'local';
const meetingsStoragePrefix = 'withdev.meetings.v1';

type TeamRole = 'owner' | 'admin' | 'member';
type MeetingStatus = 'active' | 'ended';
type MeetingFilter = 'all' | MeetingStatus;

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

function createMeetingId() {
  return `meeting_${globalThis.crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2)}`;
}

function createPlaceholderMinutes(title: string, initialTheme: string): string {
  return `${title}では「${initialTheme}」について話し合いました。現時点ではバックエンドから議事録を取得していないため、この文章はフロントエンドだけで動作確認するための仮の議事録です。実際の実装では、ミーティング終了後に生成または保存された議事録本文をバックエンドから取得し、この領域にそのまま表示します。議論の流れ、参加者の発言、合意に至った背景、次に確認したい論点などをひとつの文章として読みやすく表示できることを確認するため、少し長めの本文にしています。`;
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

function formatDateTime(value: number | null) {
  if (!value) return '-';

  return new Intl.DateTimeFormat('ja-JP', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
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

type MeetingListScreenProps = {
  user: User;
  team: UserTeamSummary;
  meetings: MeetingSummary[];
  onBackToTeams: () => void;
  onCreateMeeting: (title: string, initialTheme: string) => void;
  onOpenMeeting: (meetingId: string) => void;
  onLogout: () => void;
};

function MeetingListScreen({
  user,
  team,
  meetings,
  onBackToTeams,
  onCreateMeeting,
  onOpenMeeting,
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

      <BuildFooter />
    </main>
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
  team: UserTeamSummary;
  meeting: MeetingSummary;
  onBackToList: () => void;
  onEndMeeting: (meetingId: string) => void;
};

function MeetingRoomScreen({
  team,
  meeting,
  onBackToList,
  onEndMeeting,
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
  const [meetings, setMeetings] = useState<MeetingSummary[]>(() =>
    loadMeetings(currentUser.uid, defaultTeamId),
  );

  useEffect(() => {
    setSelectedTeamId(null);
    setSelectedMeetingId(null);
    setMeetings(loadMeetings(currentUser.uid, defaultTeamId));
  }, [currentUser.uid, defaultTeamId]);

  useEffect(() => {
    saveMeetings(currentUser.uid, meetings);
  }, [currentUser.uid, meetings]);

  const selectedTeam = teams.find((team) => team.team_id === selectedTeamId) ?? null;
  const selectedMeeting = meetings.find((meeting) => meeting.id === selectedMeetingId) ?? null;
  const teamMeetings = selectedTeam
    ? meetings.filter((meeting) => meeting.team_id === selectedTeam.team_id)
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

  const handleLogout = () => {
    void logout();
  };

  if (selectedTeam && selectedMeeting) {
    return (
      <MeetingRoomScreen
        team={selectedTeam}
        meeting={selectedMeeting}
        onBackToList={() => setSelectedMeetingId(null)}
        onEndMeeting={handleEndMeeting}
      />
    );
  }

  if (selectedTeam) {
    return (
      <MeetingListScreen
        user={currentUser}
        team={selectedTeam}
        meetings={teamMeetings}
        onBackToTeams={() => {
          setSelectedTeamId(null);
          setSelectedMeetingId(null);
        }}
        onCreateMeeting={handleCreateMeeting}
        onOpenMeeting={setSelectedMeetingId}
        onLogout={handleLogout}
      />
    );
  }

  return (
    <TeamSelectionScreen
      user={currentUser}
      teams={teams}
      onSelectTeam={setSelectedTeamId}
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
