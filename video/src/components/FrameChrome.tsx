import React from 'react';
import {AbsoluteFill, Audio, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {PassportFrame, TimingSegment, VideoProps} from '../types';

const PROVENANCE_COLORS: Record<string, string> = {
  'реальные данные': '#27AE60',
  симуляция: '#2980B9',
  схема: '#8E44AD',
};

export const getCurrentState = (t: number, segments: TimingSegment[], frames: PassportFrame[]) => {
  const seg =
    segments.find((s) => t >= s.t_start && t <= s.t_end) ||
    segments.find((s) => s.pause_window && t >= s.pause_window[0] && t <= s.pause_window[1]) ||
    [...segments].reverse().find((s) => t >= s.t_start) ||
    segments[0];

  const pauseSeg = segments.find((s) => s.pause_window && t >= s.pause_window[0] && t <= s.pause_window[1]);
  const inPause = Boolean(pauseSeg?.pause_window);
  const pauseProgress =
    pauseSeg?.pause_window
      ? Math.min(1, Math.max(0, (t - pauseSeg.pause_window[0]) / Math.max(0.1, pauseSeg.pause_window[1] - pauseSeg.pause_window[0])))
      : 0;

  let activeLabel: 'реальные данные' | 'симуляция' | 'схема' = 'симуляция';
  let bestDist = 1e9;
  for (const f of frames) {
    const d = Math.abs(f.t - t);
    if (d < bestDist) {
      bestDist = d;
      activeLabel = f.label;
    }
  }
  return {seg, inPause, pauseProgress, activeLabel};
};

/** Подпись на экране: кусок реплики не длиннее 7 слов (стандарт подачи) по времени слов из синтеза; без кусков — вся реплика. */
export const captionAt = (seg: TimingSegment | null | undefined, t: number): string => {
  if (!seg) return '';
  const caps = seg.captions || [];
  const cap = caps.length ? [...caps].reverse().find((c) => c.start <= t) || caps[0] : null;
  return cap ? cap.text : seg.spoken_ref.replace(/\+/g, '').replace(/́/g, '');
};

export const FrameChrome: React.FC<{
  props: VideoProps;
  scaleText: string;
  isTrueScale: boolean;
  provenanceOverride?: 'реальные данные' | 'симуляция' | 'схема';
  schoolBadge?: string;
  children: React.ReactNode;
}> = ({props, scaleText, isTrueScale, provenanceOverride, schoolBadge, children}) => {
  const frame = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const t = frame / fps;
  const isPortrait = height > width;
  const {seg, inPause, pauseProgress, activeLabel} = getCurrentState(
    t,
    props.timings.segments,
    props.passport.frames || [],
  );

  const label = provenanceOverride || activeLabel;
  const provBg = PROVENANCE_COLORS[label] || '#2980B9';
  const sg = props.styleguide;
  const padX = isPortrait ? 64 : 70;
  const padTop = isPortrait ? 130 : 38;
  const bottomSafe = isPortrait ? 270 : 54;

  const captionText = (seg?.on_screen || props.passport.node_card.title || '').trim();
  const subText = captionAt(seg, t);
  const totalDur = Math.max(1, props.timings.total_duration_sec || props.scene.duration_sec);
  const progressPct = Math.min(100, Math.max(0, (t / totalDur) * 100));

  return (
    <AbsoluteFill
      style={{
        backgroundColor: sg.background || '#0F1419',
        color: sg.text || '#ECEFF4',
        fontFamily: "'Segoe UI', 'Noto Sans', -apple-system, BlinkMacSystemFont, sans-serif",
      }}
    >
      {props.audioSrc ? <Audio src={staticFile(props.audioSrc)} /> : null}

      {/* Верхняя полоса метаданных (безопасная зона) */}
      <div
        style={{
          position: 'absolute',
          top: padTop,
          left: padX,
          right: padX,
          display: 'flex',
          flexDirection: 'column',
          gap: 14,
          zIndex: 10,
        }}
      >
        <div style={{display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12}}>
          <div style={{display: 'flex', alignItems: 'center', gap: 12}}>
            <span
              style={{
                backgroundColor: provBg,
                color: '#FFFFFF',
                fontSize: isPortrait ? 26 : 22,
                fontWeight: 700,
                padding: '8px 20px',
                borderRadius: 999,
                letterSpacing: 0.3,
              }}
            >
              {label}
            </span>
            <span
              style={{
                backgroundColor: isTrueScale ? '#1F4D3A' : '#25303C',
                border: `2px solid ${isTrueScale ? '#2ECC71' : '#4C5E70'}`,
                color: '#ECEFF4',
                fontSize: isPortrait ? 24 : 20,
                fontWeight: 600,
                padding: '7px 18px',
                borderRadius: 999,
              }}
            >
              {scaleText}
            </span>
          </div>

          {schoolBadge ? (
            <span
              style={{
                color: '#9FB0C0',
                fontSize: isPortrait ? 22 : 19,
                fontWeight: 600,
                backgroundColor: '#161D24',
                padding: '6px 14px',
                borderRadius: 8,
                border: '1px solid #2A3642',
              }}
            >
              {schoolBadge}
            </span>
          ) : null}
        </div>

        {/* Крупный экранный заголовок (≤ 7 слов, ≥ 44px) */}
        <div
          style={{
            fontSize: isPortrait ? 48 : 44,
            lineHeight: 1.15,
            fontWeight: 800,
            color: inPause ? '#F1C40F' : '#FFFFFF',
            textShadow: '0 2px 10px rgba(0,0,0,0.45)',
          }}
        >
          {captionText}
        </div>
      </div>

      {/* Основная область сцены */}
      <div
        style={{
          position: 'absolute',
          top: isPortrait ? 310 : 158,
          left: padX,
          right: padX,
          bottom: bottomSafe + (isPortrait ? 190 : 132),
          display: 'flex',
          alignItems: 'stretch',
          justifyContent: 'center',
        }}
      >
        {children}
      </div>

      {/* Оверлей паузы предсказания (1.5 с) */}
      {inPause ? (
        <div
          style={{
            position: 'absolute',
            right: padX + 20,
            top: isPortrait ? 330 : 174,
            backgroundColor: 'rgba(15, 20, 25, 0.92)',
            border: '3px solid #F1C40F',
            borderRadius: 18,
            padding: '14px 22px',
            display: 'flex',
            alignItems: 'center',
            gap: 16,
            zIndex: 20,
          }}
        >
          <svg width={56} height={56} viewBox="0 0 56 56">
            <circle cx={28} cy={28} r={22} fill="none" stroke="#334150" strokeWidth={6} />
            <circle
              cx={28}
              cy={28}
              r={22}
              fill="none"
              stroke="#F1C40F"
              strokeWidth={6}
              strokeDasharray={2 * Math.PI * 22}
              strokeDashoffset={2 * Math.PI * 22 * pauseProgress}
              transform="rotate(-90 28 28)"
              strokeLinecap="round"
            />
            <text x={28} y={34} textAnchor="middle" fill="#F1C40F" fontSize={18} fontWeight={800}>
              {(1.5 * (1 - pauseProgress)).toFixed(1)}
            </text>
          </svg>
          <div style={{fontSize: isPortrait ? 26 : 22, fontWeight: 700, color: '#F1C40F'}}>
            Предскажите ответ!
          </div>
        </div>
      ) : null}

      {/* Субтитры + прогресс-бар в нижней безопасной зоне */}
      <div
        style={{
          position: 'absolute',
          left: padX,
          right: padX,
          bottom: bottomSafe,
          display: 'flex',
          flexDirection: 'column',
          gap: 12,
          zIndex: 15,
        }}
      >
        <div
          style={{
            backgroundColor: 'rgba(20, 27, 34, 0.92)',
            border: '1px solid #2E3B49',
            borderRadius: 14,
            padding: isPortrait ? '16px 22px' : '12px 24px',
            fontSize: isPortrait ? 30 : 27,
            lineHeight: 1.32,
            color: '#ECEFF4',
            textAlign: 'center',
            boxShadow: '0 6px 20px rgba(0,0,0,0.45)',
          }}
        >
          {subText}
        </div>
        <div style={{height: 6, backgroundColor: '#25303C', borderRadius: 3, overflow: 'hidden'}}>
          <div style={{width: `${progressPct}%`, height: '100%', backgroundColor: provBg}} />
        </div>
      </div>
    </AbsoluteFill>
  );
};
