export function Logo({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 40 40"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
    >
      <defs>
        <linearGradient id="logo-glow" x1="0" y1="0" x2="40" y2="40" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="oklch(0.75 0.18 30)" />
          <stop offset="50%" stopColor="oklch(0.65 0.16 40)" />
          <stop offset="100%" stopColor="oklch(0.55 0.14 50)" />
        </linearGradient>
        <linearGradient id="logo-accent" x1="0" y1="40" x2="40" y2="0" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="oklch(0.80 0.12 45)" />
          <stop offset="100%" stopColor="oklch(0.60 0.18 30)" />
        </linearGradient>
        <filter id="logo-shadow">
          <feDropShadow dx="0" dy="1" stdDeviation="1.5" floodColor="oklch(0.3 0.05 40)" floodOpacity="0.3" />
        </filter>
      </defs>

      {/* Outer ring */}
      <circle cx="20" cy="20" r="18" stroke="url(#logo-glow)" strokeWidth="2.5" opacity="0.9" />

      {/* Center hub */}
      <circle cx="20" cy="20" r="4.5" fill="url(#logo-glow)" filter="url(#logo-shadow)" />

      {/* Node positions: top, bottom-left, bottom-right */}
      {/* Spokes from center to nodes */}
      <line x1="20" y1="15.5" x2="20" y2="6" stroke="url(#logo-glow)" strokeWidth="2" strokeLinecap="round" />
      <line x1="16.1" y1="22.25" x2="8.86" y2="30.5" stroke="url(#logo-glow)" strokeWidth="2" strokeLinecap="round" />
      <line x1="23.9" y1="22.25" x2="31.14" y2="30.5" stroke="url(#logo-glow)" strokeWidth="2" strokeLinecap="round" />

      {/* Outer nodes */}
      <circle cx="20" cy="5" r="3.2" fill="url(#logo-accent)" filter="url(#logo-shadow)" />
      <circle cx="8" cy="31.5" r="3.2" fill="url(#logo-accent)" filter="url(#logo-shadow)" />
      <circle cx="32" cy="31.5" r="3.2" fill="url(#logo-accent)" filter="url(#logo-shadow)" />

      {/* Arc connectors between outer nodes */}
      <path
        d="M 16.5 5.8 A 16 16 0 0 0 6.2 28.5"
        stroke="url(#logo-glow)"
        strokeWidth="1.5"
        strokeLinecap="round"
        opacity="0.5"
        strokeDasharray="3 3"
      />
      <path
        d="M 23.5 5.8 A 16 16 0 0 1 33.8 28.5"
        stroke="url(#logo-glow)"
        strokeWidth="1.5"
        strokeLinecap="round"
        opacity="0.5"
        strokeDasharray="3 3"
      />
      <path
        d="M 11 33.2 A 16 16 0 0 0 29 33.2"
        stroke="url(#logo-glow)"
        strokeWidth="1.5"
        strokeLinecap="round"
        opacity="0.5"
        strokeDasharray="3 3"
      />
    </svg>
  );
}
