import React from 'react';
import {AbsoluteFill, Audio, Easing, interpolate, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {VideoProps} from '../types';
import {getCurrentState} from '../components/FrameChrome';

/**
 * Ролик по узлу слоя: рисунок шага (те же кадры, что в курсе) меняется вместе с голосом,
 * снизу накапливается карта шагов, вопрос-предсказание останавливает рассказ.
 * Ничего, чего нет в сценарии и описании рисунка, на экране не появляется.
 */

type StoryLine = {kind: string; frame: number; on_screen: string; page?: number | null; claim_kind?: string};

const PROVENANCE: Record<string, string> = {'реальные данные': '#27AE60', симуляция: '#2980B9', схема: '#8E44AD'};
const CLAIM_KIND: Record<string, string> = {observation: 'наблюдение автора', inference: 'вывод автора', assumption: 'допущение автора'};
const ACCENT: Record<string, string> = {
  hook: '#F1C40F', predict: '#F1C40F', answer: '#2ECC71', limit: '#E67E22', data: '#4DC4BF', analogy: '#A78BFA', now: '#95A5A6', transfer: '#F1C40F',
};
const TAG: Record<string, string> = {
  hook: 'Вопрос', context: 'Зачем', mechanism: 'Как работает', why: 'Иначе', data: 'Что видно в данных', limit: 'Где не работает',
  now: 'Как сейчас', analogy: 'Похоже на', predict: 'Предскажите', answer: 'Ответ', transfer: 'Перенос',
};
const STEPS = new Set(['context', 'mechanism', 'why', 'data', 'limit', 'answer']);

const Picture: React.FC<{svg: string; width: number; height: number}> = ({svg, width, height}) => {
  const m = svg.match(/viewBox="[\d.-]+ [\d.-]+ ([\d.]+) ([\d.]+)"/);
  const [vw, vh] = m ? [Number(m[1]), Number(m[2])] : [560, 560];
  const k = Math.min(width / vw, height / vh);
  const sized = svg.replace(/width="[\d.]+" height="[\d.]+"/, `width="${vw * k}" height="${vh * k}"`);
  return <div style={{width: vw * k, height: vh * k}} dangerouslySetInnerHTML={{__html: sized}} />;
};

export const NodeStory: React.FC<{props: VideoProps}> = ({props}) => {
  const frame = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const t = frame / fps;
  const portrait = height > width;
  const segments = props.timings.segments;
  const {seg, inPause, pauseProgress, activeLabel} = getCurrentState(t, segments, props.passport.frames || []);
  const idx = seg?.index ?? 0;
  const st = props.scene.state_sample || {};
  const story: StoryLine[] = st.story || [];
  const pics: {label: string; svg: string}[] = st.visual_frames || [];
  const line = story[idx] || {kind: 'context', frame: 1, on_screen: seg?.on_screen || ''};
  const sg = props.styleguide || {};
  const ink = sg.text || '#ECEFF4';
  const panel = sg.panel || '#1A2129';
  const bg = sg.background || '#0F1419';
  const accent = ACCENT[line.kind] || '#4DC4BF';
  const total = Math.max(1, props.timings.total_duration_sec || props.scene.duration_sec);

  // кадр рисунка: плавная смена в начале фразы
  const prev = idx > 0 ? (story[idx - 1]?.frame ?? line.frame) : line.frame;
  const fade = seg ? interpolate(t, [seg.t_start, seg.t_start + 0.55], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.inOut(Easing.cubic)}) : 1;
  const cur = Math.min(Math.max(1, line.frame), Math.max(1, pics.length)) - 1;
  const was = Math.min(Math.max(1, prev), Math.max(1, pics.length)) - 1;
  const intro = interpolate(t, [0.15, 1.5], [0, 100], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)});

  // карта шагов накапливается
  const steps = story.map((s, i) => ({...s, i})).filter((s) => STEPS.has(s.kind));
  const shown = steps.filter((s) => s.i <= idx);
  const room = portrait ? 3 : 5;
  const finale = line.kind === 'transfer';
  const visible = shown.slice(Math.max(0, shown.length - room));
  // финальная карта помещается целиком: больше шагов — больше колонок и мельче шрифт; сверх предела — равномерная выборка
  const cap = portrait ? 10 : 18;
  const mapSteps = steps.length <= cap ? steps : Array.from({length: cap}, (_, k) => steps[Math.round((k * (steps.length - 1)) / (cap - 1))]);
  const cols = portrait ? (mapSteps.length > 4 ? 2 : 1) : mapSteps.length > 10 ? 3 : mapSteps.length > 6 ? 2 : 1;
  const dense = cols === 3 || (portrait && mapSteps.length > 6);

  const padX = portrait ? 64 : 70;
  const top = portrait ? 110 : 36;
  const bottom = portrait ? 250 : 46;
  const labelBg = PROVENANCE[activeLabel] || '#8E44AD';
  const picW = portrait ? width - padX * 2 - 40 : 1060;
  const picH = portrait ? 700 : 660;
  const pop = seg ? interpolate(t, [seg.t_start, seg.t_start + 0.3], [0.96, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}) : 1;
  const pageText = line.page ? `стр. ${line.page}` : '';
  const kindText = CLAIM_KIND[line.claim_kind || ''] || '';

  const question = inPause || line.kind === 'predict';
  const chips = (
    <div style={finale ? {display: 'grid', gridTemplateColumns: `repeat(${cols}, 1fr)`, gap: dense ? 8 : 10} : {display: 'flex', flexDirection: 'column', gap: portrait ? 12 : 14}}>
      {(finale ? mapSteps : visible).map((s) => {
        const active = s.i === idx;
        const n = steps.findIndex((x) => x.i === s.i) + 1;
        const born = segments[s.i]?.t_start ?? 0;
        const o = finale ? 1 : interpolate(t, [born, born + 0.35], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
        return (
          <div
            key={s.i}
            style={{
              opacity: o, transform: `translateY(${(1 - o) * 18}px)`, display: 'flex', alignItems: 'center',
              backgroundColor: active ? '#22303D' : '#141B22', border: `2px solid ${active ? accent : '#2A3642'}`, borderRadius: 14,
              padding: finale ? (dense ? '5px 9px' : '7px 14px') : '12px 18px', gap: finale && dense ? 10 : 18,
            }}
          >
            <span
              style={{
                minWidth: finale ? (dense ? 28 : 34) : 42, height: finale ? (dense ? 28 : 34) : 42, borderRadius: 22, backgroundColor: active ? accent : '#2E3B49', color: active ? '#0F1419' : ink,
                display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: finale ? (dense ? 16 : 19) : 23, fontWeight: 800,
              }}
            >
              {n}
            </span>
            <span style={{fontSize: finale ? (dense ? (portrait ? 21 : 17) : portrait ? 23 : 21) : portrait ? 31 : 27, lineHeight: 1.16, fontWeight: active ? 700 : 500, color: active ? '#FFFFFF' : '#B8C4D0'}}>{s.on_screen}</span>
          </div>
        );
      })}
    </div>
  );

    return (
    <AbsoluteFill style={{backgroundColor: bg, color: ink, fontFamily: "'Segoe UI', 'Noto Sans', -apple-system, BlinkMacSystemFont, sans-serif"}}>
      {props.audioSrc ? <Audio src={staticFile(props.audioSrc)} /> : null}

      <div style={{position: 'absolute', top, left: padX, right: padX, display: 'flex', flexDirection: 'column', gap: portrait ? 18 : 14}}>
        <div style={{display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap'}}>
          <span style={{backgroundColor: labelBg, color: '#FFFFFF', fontSize: portrait ? 28 : 24, fontWeight: 700, padding: '7px 20px', borderRadius: 999}}>{activeLabel}</span>
          <span style={{backgroundColor: '#25303C', border: '2px solid #4C5E70', fontSize: portrait ? 25 : 21, fontWeight: 600, padding: '6px 18px', borderRadius: 999}}>{st.scale_note}</span>
        </div>
        <div style={{display: 'flex', flexDirection: 'column', gap: portrait ? 8 : 4, transform: `scale(${pop})`, transformOrigin: 'left top'}}>
          <span style={{fontSize: portrait ? 28 : 23, fontWeight: 800, color: accent, letterSpacing: 1}}>
            {TAG[line.kind] || ''}
            {pageText ? <span style={{fontWeight: 500, color: '#9FB0C0'}}>{`  ·  ${pageText}${kindText ? `, ${kindText}` : ''}`}</span> : null}
          </span>
          <span style={{fontSize: portrait ? 52 : 44, lineHeight: 1.1, fontWeight: 800, color: question ? '#F1C40F' : '#FFFFFF'}}>{line.on_screen}</span>
        </div>
      </div>

      <div
        style={{
          position: 'absolute', top: portrait ? 385 : 200, left: padX, right: padX, bottom: bottom + (portrait ? 200 : 128),
          display: 'flex', flexDirection: portrait ? 'column' : 'row', gap: portrait ? 26 : 36,
        }}
      >
        <div
          style={{
            position: 'relative', flex: 'none', width: portrait ? '100%' : picW + 40, height: portrait ? picH + 30 : '100%', backgroundColor: panel, borderRadius: 20,
            border: `2px solid ${question ? '#F1C40F' : '#2E3B49'}`, overflow: 'hidden',
          }}
        >
          {pics.length ? (
            [was, cur].map((k, j) => (
              <div
                key={j}
                style={{
                  position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
                  opacity: j === 0 ? (was === cur ? 0 : 1 - fade) : was === cur ? 1 : fade,
                  clipPath: idx === 0 ? `inset(0 ${100 - intro}% 0 0)` : undefined,
                }}
              >
                <Picture svg={pics[k].svg} width={picW} height={picH} />
              </div>
            ))
          ) : null}
          {pics.length > 1 && st.frames_label && pics[cur].label ? (
            <div style={{position: 'absolute', right: 18, top: 16, display: 'flex', gap: 10, alignItems: 'center', backgroundColor: 'rgba(15,20,25,0.85)', borderRadius: 12, padding: '7px 16px'}}>
              <span style={{fontSize: portrait ? 24 : 20, color: '#9FB0C0'}}>{st.frames_label}:</span>
              <span style={{fontSize: portrait ? 26 : 22, fontWeight: 700}}>{pics[cur].label}</span>
            </div>
          ) : null}
        </div>

        <div style={{flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', justifyContent: 'flex-start'}}>
          {question ? (
            <div style={{backgroundColor: '#252112', border: '3px solid #F1C40F', borderRadius: 18, padding: portrait ? '26px 30px' : '24px 28px', display: 'flex', gap: 24, alignItems: 'center'}}>
              <svg width={92} height={92} viewBox="0 0 56 56" style={{flex: 'none'}}>
                <circle cx={28} cy={28} r={22} fill="none" stroke="#4A4325" strokeWidth={6} />
                <circle cx={28} cy={28} r={22} fill="none" stroke="#F1C40F" strokeWidth={6} strokeDasharray={2 * Math.PI * 22}
                  strokeDashoffset={2 * Math.PI * 22 * (inPause ? pauseProgress : 0)} transform="rotate(-90 28 28)" strokeLinecap="round" />
                <text x={28} y={35} textAnchor="middle" fill="#F1C40F" fontSize={20} fontWeight={800}>?</text>
              </svg>
              <span style={{fontSize: portrait ? 38 : 32, lineHeight: 1.25, fontWeight: 700, color: '#F7DC6F'}}>{inPause ? 'Подумайте: ответ через секунду' : 'Ваш ответ?'}</span>
            </div>
          ) : (
            chips
          )}
        </div>
      </div>

      <div style={{position: 'absolute', left: padX, right: padX, bottom, display: 'flex', flexDirection: 'column', gap: 12}}>
        <div
          style={{
            backgroundColor: 'rgba(20, 27, 34, 0.92)', border: '1px solid #2E3B49', borderRadius: 14, padding: portrait ? '18px 24px' : '12px 24px',
            fontSize: portrait ? 34 : 29, lineHeight: 1.3, textAlign: 'center',
          }}
        >
          {seg ? seg.spoken_ref.replace(/\+/g, '') : ''}
        </div>
        <div style={{display: 'flex', justifyContent: 'space-between', fontSize: portrait ? 22 : 18, color: '#7F8FA0'}}>
          <span>{st.source_line}</span>
          <span>{st.title}</span>
        </div>
        <div style={{height: 6, backgroundColor: '#25303C', borderRadius: 3, overflow: 'hidden'}}>
          <div style={{width: `${Math.min(100, (t / total) * 100)}%`, height: '100%', backgroundColor: labelBg}} />
        </div>
      </div>
    </AbsoluteFill>
  );
};
