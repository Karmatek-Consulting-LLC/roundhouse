import type { ReactNode } from "react";

// Roundhouse plan view (fan of stalls + turntable) as an empty-state
// illustration. Static, muted, theme-aware via currentColor + tokens.
function TurntableArt() {
  const bays = [-80, -60, -40, -20, 0, 20, 40, 60, 80];
  return (
    <svg
      viewBox="-30 0 660 480"
      className="mx-auto h-44 w-auto text-border"
      aria-hidden="true"
    >
      {/* engine shed band */}
      <path
        d="M 36 342.4 A 266 266 0 1 1 564 342.4 L 504.5 335.1 A 206 206 0 1 0 95.5 335.1 Z"
        fill="currentColor"
        fillOpacity="0.18"
        stroke="currentColor"
        strokeWidth="1.5"
      />
      {/* pit */}
      <circle
        cx="300"
        cy="310"
        r="116"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
      />
      {/* stalls + tracks */}
      {bays.map((deg) => (
        <g key={deg} transform={`rotate(${deg} 300 310)`}>
          <line
            x1="300"
            y1="190"
            x2="300"
            y2="92"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeDasharray="4 6"
          />
          <rect
            x="283"
            y="64"
            width="34"
            height="24"
            rx="3"
            fill="currentColor"
            fillOpacity="0.35"
          />
        </g>
      ))}
      {/* approach leads */}
      <line x1="300" y1="430" x2="300" y2="478" stroke="currentColor" strokeWidth="2" strokeDasharray="6 5" />
      {/* bridge, parked at the lead */}
      <g className="text-primary" stroke="currentColor">
        <line x1="291" y1="202" x2="291" y2="418" strokeWidth="2" />
        <line x1="309" y1="202" x2="309" y2="418" strokeWidth="2" />
        <line x1="300" y1="206" x2="300" y2="414" strokeWidth="26" strokeDasharray="2.5 11" opacity="0.25" />
      </g>
      <circle cx="300" cy="310" r="24" fill="none" stroke="currentColor" strokeWidth="2" className="text-primary" />
      <circle cx="300" cy="310" r="7" fill="currentColor" className="text-primary" />
    </svg>
  );
}

export function TurntableEmpty({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="rounded-lg border border-dashed p-10 text-center">
      <TurntableArt />
      <p className="mt-6 font-mono text-xs font-medium uppercase tracking-[0.2em] text-muted-foreground">
        {title}
      </p>
      {children && (
        <p className="mx-auto mt-2 max-w-md text-sm text-muted-foreground">
          {children}
        </p>
      )}
    </div>
  );
}
