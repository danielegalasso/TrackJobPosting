import { EyeOff, Play, Search, Settings, Star } from 'lucide-react';

export type View = 'search' | 'saved' | 'removed' | 'settings' | 'console';

interface HeaderProps {
  view: View;
  onNavigate: (view: View) => void;
  savedCount: number;
  /** A search or a scoring run is in progress. */
  searchRunning: boolean;
}

const NAV: { key: View; label: string; icon: typeof Search }[] = [
  { key: 'search', label: 'Search', icon: Search },
  { key: 'saved', label: 'Starred', icon: Star },
  { key: 'removed', label: 'Removed', icon: EyeOff },
  { key: 'console', label: 'Run the Search', icon: Play },
  { key: 'settings', label: 'Settings', icon: Settings },
];

export function Header({ view, onNavigate, savedCount, searchRunning }: HeaderProps) {
  return (
    <header className="bg-brand text-white shadow-sm">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6">
        <button
          onClick={() => onNavigate('search')}
          className="flex items-center gap-2 text-left"
          aria-label="findmyjob.top home"
        >
          <span className="text-xl font-bold tracking-tight sm:text-2xl">findmyjob.top</span>
          {searchRunning && (
            <span className="rounded-full bg-brand-dark px-2 py-0.5 text-[11px] font-medium text-emerald-100">
              searching…
            </span>
          )}
        </button>

        <nav className="flex items-center gap-1 text-sm font-medium sm:gap-2">
          {NAV.map(({ key, label, icon: Icon }) => (
            <button
              key={key}
              onClick={() => onNavigate(key)}
              aria-current={view === key ? 'page' : undefined}
              className={`flex items-center gap-1.5 rounded px-2.5 py-1.5 transition sm:px-3 ${
                view === key ? 'bg-brand-dark text-white' : 'text-emerald-50 hover:bg-brand-dark/60'
              }`}
            >
              <Icon className="h-4 w-4" aria-hidden />
              <span className="hidden sm:inline">{label}</span>
              {key === 'saved' && savedCount > 0 && (
                <span className="rounded bg-white/20 px-1.5 text-[11px] font-bold">{savedCount}</span>
              )}
            </button>
          ))}
        </nav>
      </div>
    </header>
  );
}
