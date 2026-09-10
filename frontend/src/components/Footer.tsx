import { FileText, Shield } from 'lucide-react';

interface FooterProps {
  onOpenLegal: (document: 'privacy' | 'terms') => void;
}

export function Footer({ onOpenLegal }: FooterProps) {
  return (
    <footer className="border-t border-gray-200 bg-gray-50 py-6 text-xs text-gray-500">
      <div className="mx-auto flex max-w-7xl flex-col items-center justify-between gap-4 px-4 sm:px-6 md:flex-row">
        <p className="text-center md:text-left">
          ACIDE-Watch — self-hosted career intelligence. Listings are aggregated from public
          employer career feeds; we are not an employer or recruiter.
        </p>
        <div className="flex flex-shrink-0 items-center gap-6">
          <button
            onClick={() => onOpenLegal('privacy')}
            className="flex items-center gap-1 transition hover:text-gray-800"
          >
            <Shield className="h-3.5 w-3.5" aria-hidden />
            <span>Privacy Policy</span>
          </button>
          <button
            onClick={() => onOpenLegal('terms')}
            className="flex items-center gap-1 transition hover:text-gray-800"
          >
            <FileText className="h-3.5 w-3.5" aria-hidden />
            <span>Terms of Use</span>
          </button>
        </div>
      </div>
    </footer>
  );
}
