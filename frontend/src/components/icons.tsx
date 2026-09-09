export function UploadIcon({ className = "" }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      <path d="M12 16V4" />
      <path d="m6 9 6-5 6 5" />
      <path d="M4 20h16" />
    </svg>
  );
}

export function DownloadIcon({ className = "" }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      <path d="M12 4v12" />
      <path d="m6 11 6 5 6-5" />
      <path d="M4 20h16" />
    </svg>
  );
}

export function CopyIcon({ className = "" }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      <rect x="9" y="9" width="12" height="12" rx="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  );
}

export function StarIcon({ className = "" }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="currentColor"
      stroke="currentColor"
      strokeWidth={1.2}
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      <path d="M12 17.27 18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z" />
    </svg>
  );
}

export function FileDocIcon({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
      <rect x="6" y="3" width="12" height="18" rx="2" />
      <path d="M10 8h4M10 12h4M10 16h2" />
    </svg>
  );
}

export function FileImageIcon({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
      <rect x="6" y="3" width="12" height="18" rx="2" />
      <circle cx="9.6" cy="8.2" r="1.3" />
      <path d="M7 17l3.2-3.2 2.3 2 2-2 2.5 3.2" />
    </svg>
  );
}

export function FileVideoIcon({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
      <rect x="6" y="3" width="12" height="18" rx="2" />
      <path d="M10.5 9v6l5-3z" />
    </svg>
  );
}

export function FileZipIcon({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
      <rect x="6" y="3" width="12" height="18" rx="2" />
      <path d="M10 6h2v2h-2v2h2v2h-2" />
      <path d="M9.5 15h5" />
    </svg>
  );
}

export function FileTextIcon({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
      <rect x="6" y="3" width="12" height="18" rx="2" />
      <path d="M9.5 8h5M9.5 12h5M9.5 16h3" />
    </svg>
  );
}

export function FileSheetIcon({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
      <rect x="6" y="3" width="12" height="18" rx="2" />
      <path d="M10 7v10M14 7v10M7 10h10M7 14h10" />
    </svg>
  );
}
