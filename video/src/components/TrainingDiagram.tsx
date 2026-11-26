import React from 'react';
import {TimingSegment} from '../types';

export const ink = '#edf3f5';
export const muted = '#b5c6ce';
export const grid = '#40545f';
export const water = '#3498db';
export const blue = '#83d3f2';
export const amber = '#ffbd68';
export const oil = '#2e8b57';
export const mint = '#79ddca';
export const panel = '#182730';
export const grain = '#c6af89';

export const fraction = (v: number) => Math.max(0, Math.min(1, v));
export const ease = (v: number) => {
  const u = fraction(v);
  return u * u * (3 - 2 * u);
};

const normalize = (s: string) =>
  s
    .toLowerCase()
    .replace(/ё/g, 'е')
    .replace(/[^а-яa-z0-9]/g, '');
export function cue(seg: TimingSegment, phrase: string): number {
  const target = phrase.split(' ').map(normalize);
  const words = seg.words.map((w) => normalize(w.word));
  const i = words.findIndex((_, i) => target.every((word, j) => words[i + j] === word));
  return i < 0 ? Infinity : seg.words[i].start;
}

export function Label({
  x,
  y,
  children,
  size = 38,
  color = ink,
  anchor = 'start',
  bold = false,
}: {
  x: number;
  y: number;
  children: React.ReactNode;
  size?: number;
  color?: string;
  anchor?: 'start' | 'middle' | 'end';
  bold?: boolean;
}) {
  return (
    <text x={x} y={y} fill={color} fontSize={size} fontWeight={bold ? 730 : 500} textAnchor={anchor}>
      {children}
    </text>
  );
}

export function Line({
  x1,
  y1,
  x2,
  y2,
  color = grid,
  weight = 3,
  dash,
}: {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  color?: string;
  weight?: number;
  dash?: string;
}) {
  return <line x1={x1} y1={y1} x2={x2} y2={y2} stroke={color} strokeWidth={weight} strokeDasharray={dash} />;
}

export function Diagram({w, h, children}: {w: number; h: number; children: React.ReactNode}) {
  return (
    <svg width="100%" height="100%" viewBox={`0 0 ${w} ${h}`} style={{fontFamily: 'Arial, sans-serif'}}>
      <defs>
        <pattern id="hatch" width="18" height="18" patternUnits="userSpaceOnUse">
          <path d="M-4 4L4 -4 M0 18L18 0 M14 22L22 14" stroke="#d5edf2" strokeOpacity=".36" strokeWidth="2" />
        </pattern>
        <pattern id="dots" width="16" height="16" patternUnits="userSpaceOnUse">
          <circle cx="5" cy="5" r="2.5" fill="#e9edee" opacity=".48" />
        </pattern>
      </defs>
      <rect width={w} height={h} rx="22" fill={panel} />
      {children}
    </svg>
  );
}
