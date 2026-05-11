import type { User } from 'firebase/auth';
import {
  Building2,
  ChevronLeft,
  ChevronRight,
  CircleUserRound,
  Loader2,
  LogIn,
  LogOut,
  ShieldCheck,
  Sparkles,
  Users,
} from 'lucide-react';
import { useMemo, useState } from 'react';
import { AuthProvider, getReadableAuthError, useAuth } from './contexts/AuthContext';

const gitSha = import.meta.env.VITE_GIT_SHA ?? 'local';
const appEnv = import.meta.env.VITE_APP_ENV ?? 'local';

type TeamRole = 'owner' | 'admin' | 'member';

type UserTeamSummary = {
  team_id: string;
  name: string;
  role: TeamRole;
  member_count: number;
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
        <span>{team.role}</span>
        <span>{team.member_count} member</span>
      </span>
      <ChevronRight size={22} />
    </button>
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

function TeamHome({ team, onBack }: { team: UserTeamSummary; onBack: () => void }) {
  return (
    <main className="app-layout">
      <button className="quiet-button" type="button" onClick={onBack}>
        <ChevronLeft size={18} />
        チーム選択
      </button>
      <section className="workspace-panel">
        <span className="workspace-icon">
          <Building2 size={28} />
        </span>
        <div>
          <p className="eyebrow">Team</p>
          <h1>{team.name}</h1>
          <p className="workspace-id">{team.team_id}</p>
        </div>
      </section>
    </main>
  );
}

function TeamSelectionScreen() {
  const { currentUser, logout } = useAuth();
  const [selectedTeamId, setSelectedTeamId] = useState<string | null>(null);
  const teams = useMemo(() => (currentUser ? [createDefaultTeam(currentUser)] : []), [currentUser]);
  const selectedTeam = teams.find((team) => team.team_id === selectedTeamId) ?? null;

  if (!currentUser) {
    return null;
  }

  if (selectedTeam) {
    return <TeamHome team={selectedTeam} onBack={() => setSelectedTeamId(null)} />;
  }

  return (
    <main className="app-layout">
      <header className="app-header">
        <div>
          <p className="eyebrow">WithDev</p>
          <h1>チームを選択</h1>
        </div>
        <div className="account-area">
          <UserAvatar user={currentUser} />
          <div className="account-copy">
            <strong>{getDisplayName(currentUser)}</strong>
            <span>{currentUser.email}</span>
          </div>
          <button className="icon-button" type="button" onClick={() => void logout()} aria-label="ログアウト">
            <LogOut size={20} />
          </button>
        </div>
      </header>

      <section className="team-list" aria-label="所属チーム">
        {teams.map((team) => (
          <TeamCard key={team.team_id} team={team} onOpen={() => setSelectedTeamId(team.team_id)} />
        ))}
      </section>

      <footer className="build-footer" aria-label="Build information">
        <span>
          <ShieldCheck size={16} />
          {appEnv}
        </span>
        <span>{shortSha(gitSha)}</span>
      </footer>
    </main>
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

  return <TeamSelectionScreen />;
}

function App() {
  return (
    <AuthProvider>
      <WithDevApp />
    </AuthProvider>
  );
}

export default App;
