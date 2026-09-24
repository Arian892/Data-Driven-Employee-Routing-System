import React from 'react';

interface RouteMapBackdropProps {
  /** 'panel' — bold version for a solid dark navy panel (login page, sidebar
   *  rails). 'content' — much fainter, theme-aware version for the main
   *  content area, which is a light cream page in light mode and near-black
   *  in dark mode, and has real UI (cards, text, forms) sitting on top of it. */
  variant?: 'panel' | 'content';
}

/** Decorative backdrop — soft blurred color blobs plus a faint winding-route
 * and stop-marker SVG motif. Built from CSS/SVG rather than a photo so it
 * never needs an external image asset. */
export const RouteMapBackdrop: React.FC<RouteMapBackdropProps> = ({ variant = 'panel' }) => {
  const isContent = variant === 'content';
  return (
    <div className="absolute inset-0 overflow-hidden pointer-events-none">
      <div
        className={`absolute -top-24 -left-20 w-[26rem] h-[26rem] rounded-full blur-3xl ${isContent ? 'opacity-[0.07] dark:opacity-[0.12]' : 'opacity-25'}`}
        style={{ background: '#14B8A6' }}
      />
      <div
        className={`absolute top-1/3 -right-28 w-96 h-96 rounded-full blur-3xl ${isContent ? 'opacity-[0.06] dark:opacity-[0.10]' : 'opacity-20'}`}
        style={{ background: '#3B82F6' }}
      />
      <div
        className={`absolute -bottom-32 left-1/4 w-[28rem] h-[28rem] rounded-full blur-3xl ${isContent ? 'opacity-[0.07] dark:opacity-[0.12]' : 'opacity-20'}`}
        style={{ background: '#14B8A6' }}
      />
      <svg
        className={`absolute inset-0 w-full h-full blur-[1px] ${isContent ? 'text-foreground opacity-[0.05] dark:opacity-[0.07]' : 'text-white opacity-[0.14]'}`}
        viewBox="0 0 600 800"
        fill="none"
        preserveAspectRatio="xMidYMid slice"
        xmlns="http://www.w3.org/2000/svg"
      >
        <path d="M -20 120 C 100 80, 180 200, 300 160 S 480 60, 620 140" stroke="currentColor" strokeWidth="2" />
        <path d="M -20 340 C 120 300, 200 420, 340 380 S 520 300, 620 360" stroke="currentColor" strokeWidth="2" />
        <path d="M -20 560 C 140 520, 220 640, 360 600 S 540 520, 620 580" stroke="currentColor" strokeWidth="2" />
        <path d="M 80 -20 C 120 120, 60 260, 140 380 S 100 600, 160 820" stroke="currentColor" strokeWidth="2" />
        <path d="M 420 -20 C 460 140, 400 280, 470 400 S 430 620, 480 820" stroke="currentColor" strokeWidth="2" />
        <circle cx="300" cy="160" r="5" fill="#14B8A6" />
        <circle cx="340" cy="380" r="5" fill="#14B8A6" />
        <circle cx="360" cy="600" r="5" fill="#14B8A6" />
        <circle cx="140" cy="380" r="4" fill="currentColor" />
        <circle cx="470" cy="400" r="4" fill="currentColor" />
      </svg>
    </div>
  );
};
