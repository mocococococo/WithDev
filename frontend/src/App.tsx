import {
  Activity,
  ArrowRight,
  BadgeCheck,
  Cloud,
  ExternalLink,
  GitBranch,
  GitMerge,
  Rocket,
  ShieldCheck,
} from 'lucide-react';

const gitSha = import.meta.env.VITE_GIT_SHA ?? 'local';
const appEnv = import.meta.env.VITE_APP_ENV ?? 'local';

const environments = [
  {
    name: 'Development',
    branch: 'develop',
    project: 'withdev-dev',
    service: 'withdev-dev',
    registry: 'withdev-dev/frontend',
    cadence: 'Push deploy',
    tone: 'emerald',
  },
  {
    name: 'Production',
    branch: 'main',
    project: 'withdev-prod',
    service: 'withdev-prod',
    registry: 'withdev-prod/frontend',
    cadence: 'Merge deploy',
    tone: 'amber',
  },
];

const checks = [
  'GitHub Actions',
  'Workload Identity Federation',
  'Artifact Registry',
  'Cloud Run',
];

function shortSha(value: string) {
  return value === 'local' ? value : value.slice(0, 7);
}

function App() {
  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">WithDev</p>
          <h1>Delivery Board</h1>
        </div>
        <a
          className="icon-link"
          href="https://github.com/mocococococo/WithDev"
          rel="noreferrer"
          target="_blank"
          aria-label="Open GitHub repository"
        >
          <ExternalLink size={18} />
          GitHub
        </a>
      </header>

      <section className="status-band" aria-label="Current build">
        <div>
          <span className="status-icon">
            <Activity size={20} />
          </span>
          <div>
            <p className="label">Runtime</p>
            <strong>{appEnv}</strong>
          </div>
        </div>
        <div>
          <span className="status-icon">
            <GitBranch size={20} />
          </span>
          <div>
            <p className="label">Revision</p>
            <strong>{shortSha(gitSha)}</strong>
          </div>
        </div>
        <div>
          <span className="status-icon">
            <ShieldCheck size={20} />
          </span>
          <div>
            <p className="label">Auth</p>
            <strong>Keyless</strong>
          </div>
        </div>
      </section>

      <section className="environment-grid" aria-label="Deployment environments">
        {environments.map((environment) => (
          <article className={`environment-card ${environment.tone}`} key={environment.name}>
            <div className="card-title">
              <span>
                <Cloud size={20} />
              </span>
              <div>
                <p className="label">{environment.cadence}</p>
                <h2>{environment.name}</h2>
              </div>
            </div>
            <dl className="metadata">
              <div>
                <dt>Branch</dt>
                <dd>{environment.branch}</dd>
              </div>
              <div>
                <dt>Project</dt>
                <dd>{environment.project}</dd>
              </div>
              <div>
                <dt>Cloud Run</dt>
                <dd>{environment.service}</dd>
              </div>
              <div>
                <dt>Image</dt>
                <dd>{environment.registry}</dd>
              </div>
            </dl>
          </article>
        ))}
      </section>

      <section className="workflow-row" aria-label="Release workflow">
        <div className="flow-item">
          <GitBranch size={22} />
          <span>develop</span>
        </div>
        <ArrowRight size={18} />
        <div className="flow-item">
          <Rocket size={22} />
          <span>withdev-dev</span>
        </div>
        <ArrowRight size={18} />
        <div className="flow-item">
          <GitMerge size={22} />
          <span>main</span>
        </div>
        <ArrowRight size={18} />
        <div className="flow-item">
          <BadgeCheck size={22} />
          <span>withdev-prod</span>
        </div>
      </section>

      <section className="check-strip" aria-label="Platform checks">
        {checks.map((check) => (
          <span key={check}>
            <BadgeCheck size={16} />
            {check}
          </span>
        ))}
      </section>
    </main>
  );
}

export default App;
