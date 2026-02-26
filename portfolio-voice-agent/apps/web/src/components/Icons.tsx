type IconProps = { size?: number; className?: string };

const base = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 2,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const
};

export function MicIcon({ size = 24, className }: IconProps) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24" {...base} aria-hidden="true">
      <path d="M12 2a3 3 0 0 0-3 3v6a3 3 0 1 0 6 0V5a3 3 0 0 0-3-3z" />
      <path d="M19 10v1a7 7 0 0 1-14 0v-1" />
      <line x1="12" y1="18" x2="12" y2="22" />
      <line x1="8" y1="22" x2="16" y2="22" />
    </svg>
  );
}

export function Volume2Icon({ size = 16, className }: IconProps) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24" {...base} aria-hidden="true">
      <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
      <path d="M15.5 8.5a5 5 0 0 1 0 7" />
      <path d="M18.5 6a8.5 8.5 0 0 1 0 12" />
    </svg>
  );
}

export function HandIcon({ size = 16, className }: IconProps) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24" {...base} aria-hidden="true">
      <path d="M7 11V5a1 1 0 0 1 2 0v6" />
      <path d="M11 11V4a1 1 0 0 1 2 0v7" />
      <path d="M15 11V6a1 1 0 0 1 2 0v8" />
      <path d="M19 14a4 4 0 0 1-4 4h-3.5a4.5 4.5 0 0 1-4.2-2.8L5.7 11a1 1 0 1 1 1.8-.8l1.2 2.4V8a1 1 0 1 1 2 0v3" />
    </svg>
  );
}
