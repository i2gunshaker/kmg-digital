import React from 'react';
import {interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import {VideoProps} from '../types';
import {FrameChrome, getCurrentState} from '../components/FrameChrome';

export const FieldDiffusion2D: React.FC<{props: VideoProps}> = ({props}) => {
  const frame = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const t = frame / fps;
  const isPortrait = height > width;
  const {seg, inPause} = getCurrentState(t, props.timings.segments, props.passport.frames || []);
  const segIdx = seg?.index ?? 0;

  const state = props.scene.state_sample || {};
  const profiles: any[] = state.profiles || [];
  const sg = props.styleguide;

  // Сегмент 3: схлопывание в истинный масштаб 1:1
  const seg3 = props.timings.segments[3];
  const inTrueScale = segIdx === 3;
  const collapseT = seg3 && segIdx === 3
    ? interpolate(t, [seg3.t_start, seg3.t_start + 1.0, seg3.t_end - 0.8, seg3.t_end], [0, 1, 1, 0], {
        extrapolateLeft: 'clamp',
        extrapolateRight: 'clamp',
      })
    : 0;
  const scaleFactor = Math.round(interpolate(collapseT, [0, 1], [20, 1]));
  const scaleText = inTrueScale ? 'Истинный масштаб 1:1 (20 м / 1000 м)' : `Вертикальное преувеличение ×${scaleFactor}`;

  // Сжатие пор и рост воронки p(r, t) плавно по времени
  const totalDur = Math.max(1, props.timings.total_duration_sec || 110);
  const simTimeSec = interpolate(t, [0, totalDur], [1, 3600], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const rFrontM = Math.sqrt(0.01 + 4.0 * 1.0 * simTimeSec); // м, R(t)=sqrt(rc^2 + 4*kappa*t)
  const frontFrac = Math.min(1, rFrontM / 120.0);

  const panelW = isPortrait ? 952 : 1780;
  const panelH = isPortrait ? 1110 : 710;

  // Левая половина (разрез пласта + микропора ×10 000), правая (графики p(r,t) и R(t))
  const leftX = 32;
  const leftW = isPortrait ? panelW - 64 : 830;
  const rightX = isPortrait ? 32 : 890;
  const rightW = isPortrait ? panelW - 64 : panelW - 890 - 32;

  const slabTop = 96;
  const slabMaxH = isPortrait ? 300 : 320;
  const slabH = interpolate(collapseT, [0, 1], [slabMaxH, 16]);
  const slabY = slabTop + (slabMaxH - slabH) / 2;

  const plotTop = isPortrait ? 560 : 96;
  const plotH = isPortrait ? 240 : 260;
  const rPlotTop = isPortrait ? 840 : 415;
  const rPlotH = isPortrait ? 230 : 250;

  const profColors = ['#5DADE2', '#00CEC9', '#F1C40F', '#2ECC71'];

  // Оценка ответа после паузы предсказания (segIdx >= 7)
  const showDoubleAnswer = segIdx >= 7 && !inPause;
  const segs = props.timings.segments;
  const segStart = (i: number) => segs[Math.min(i, segs.length - 1)]?.t_start ?? 0;
  const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;
  const answerT = showDoubleAnswer ? interpolate(t, [segStart(7), segStart(7) + 0.5], [0, 1], clamp) : 0;

  // Последовательное проявление профилей -Ei: 1-й сразу, 2-й с seg 2, 3-й с seg 4, 4-й с seg 5
  const profileRevealSeg = [0, 2, 4, 5];
  const profileReveal = (idx: number) => {
    const s = profileRevealSeg[idx] ?? 0;
    if (s === 0) return 1;
    return interpolate(t, [segStart(s), segStart(s) + 1.2], [0, 1], clamp);
  };
  // Кривая √t прорисовывается в seg 6 (до паузы) и остаётся
  const sqrtDraw = segs[6] ? interpolate(t, [segStart(6), segStart(6) + 4.0], [0, 1], clamp) : 1;

  // Фокус внимания: активная панель подсвечена, остальные слегка приглушены
  type PanelKey = 'slab' | 'pores' | 'ei' | 'rt';
  const focusFor = (i: number): PanelKey[] | null => {
    if (i <= 2) return ['slab', 'pores'];
    if (i === 3) return ['slab'];
    if (i <= 5) return ['ei'];
    if (i <= 7) return ['rt'];
    return null; // seg 8–9: всё без приглушения
  };
  const curFocus = focusFor(segIdx);
  const prevFocus = segIdx > 0 ? focusFor(segIdx - 1) : curFocus;
  const focusBlend = seg ? interpolate(t, [seg.t_start, seg.t_start + 0.5], [0, 1], clamp) : 1;
  const levelOf = (f: PanelKey[] | null, k: PanelKey) => (f === null || f.includes(k) ? 1 : 0);
  const glowOf = (f: PanelKey[] | null, k: PanelKey) => (f !== null && f.includes(k) ? 1 : 0);
  const mix = (a: number, b: number) => a + (b - a) * focusBlend;
  const panelOpacity = (k: PanelKey) => 0.72 + 0.28 * mix(levelOf(prevFocus, k), levelOf(curFocus, k));
  const panelGlow = (k: PanelKey) => mix(glowOf(prevFocus, k), glowOf(curFocus, k));
  const glowRect = (k: PanelKey, x: number, y: number, w: number, h: number, color: string) => {
    const g = panelGlow(k);
    return g > 0.01 ? (
      <rect x={x} y={y} width={w} height={h} rx={16} fill="none" stroke={color} strokeWidth={3.5} strokeOpacity={0.9 * g} />
    ) : null;
  };

  return (
    <FrameChrome
      props={props}
      scaleText={scaleText}
      isTrueScale={inTrueScale}
      provenanceOverride={segIdx === 0 || segIdx === 3 || segIdx === 8 ? 'схема' : 'симуляция'}
      schoolBadge="СИ: МПа, м²/с (модель -Ei / ПССС)"
    >
      <svg viewBox={`0 0 ${panelW} ${panelH}`} width="100%" height="100%">
        <rect x={0} y={0} width={panelW} height={panelH} rx={20} fill={sg.panel || '#1A2129'} stroke="#2E3B49" strokeWidth={2} />

        {/* Подсветка активных панелей */}
        {glowRect('slab', leftX - 14, 18, leftW + 28, slabTop + slabMaxH + 20 - 18, '#00CEC9')}
        {!isPortrait ? glowRect('pores', leftX - 4, slabTop + slabMaxH + 34 - 4, leftW + 8, 233, '#00CEC9') : null}
        {glowRect('ei', rightX - 4, plotTop - 4, rightW + 8, plotH + 8, '#00CEC9')}
        {glowRect('rt', rightX - 4, rPlotTop - 4, rightW + 8, rPlotH + 8, '#F1C40F')}

        {/* 1. Разрез упругого пласта с покрышкой (глубина вниз) */}
        <g opacity={panelOpacity('slab')}>
        <text x={leftX} y={56} fill="#FFFFFF" fontSize={26} fontWeight={700}>
          Упругий пласт: воронка возмущения R(t) ≈ {rFrontM.toFixed(0)} м
        </text>
        <rect x={leftX} y={slabY} width={leftW} height={Math.max(5, slabH * 0.25)} fill={sg.lithology?.shale?.color || '#6B8E5A'} />
        <rect x={leftX} y={slabY + Math.max(5, slabH * 0.25)} width={leftW} height={Math.max(6, slabH * 0.75)} fill={sg.lithology?.sand?.color || '#E8C872'} />

        {/* Возмущённая зона вокруг скважины слева */}
        <rect
          x={leftX}
          y={slabY + Math.max(5, slabH * 0.25)}
          width={Math.max(16, leftW * frontFrac)}
          height={Math.max(6, slabH * 0.75)}
          fill="#00CEC9"
          fillOpacity={0.34}
          stroke="#00CEC9"
          strokeWidth={2}
        />
        {/* Скважина и стрелки притока от высокого p_к к низкому p_c */}
        <rect x={leftX + 18} y={slabY - 12} width={18} height={slabH + 24} fill="#1E2730" stroke="#F1C40F" strokeWidth={2} />
        {!inTrueScale ? (
          <g>
            <text x={leftX + 52} y={slabY + 28} fill="#0F1419" fontSize={20} fontWeight={800}>
              Непроницаемая покрышка (сверху)
            </text>
            <text x={leftX + 52} y={slabY + slabH * 0.62} fill="#0F1419" fontSize={22} fontWeight={800}>
              ← Приток нефти от pк = 25 МПа к забою pс
            </text>
          </g>
        ) : (
          <text x={leftX + leftW / 2} y={slabY + slabH + 46} textAnchor="middle" fill="#2ECC71" fontSize={23} fontWeight={700}>
            1:1 — пласт 20 м на 1000 м длины тоньше листа бумаги
          </text>
        )}
        </g>

        {/* Врезка микропор (×10 000): сжатие скелета + расширение нефти */}
        {!isPortrait ? (
          <g transform={`translate(${leftX}, ${slabTop + slabMaxH + 34})`} opacity={panelOpacity('pores')}>
            <rect x={0} y={0} width={leftW} height={225} rx={14} fill="#121820" stroke="#364656" strokeWidth={1.5} />
            <text x={20} y={36} fill="#FFFFFF" fontSize={23} fontWeight={700}>
              Врезка пор (увеличено ×10 000): горное давление сжимает скелет
            </text>
            {[0, 1, 2, 3, 4].map((k) => {
              const cx = 95 + k * 145;
              const squeeze = 1 - 0.16 * frontFrac;
              return (
                <g key={k}>
                  <circle cx={cx} cy={126} r={42 * squeeze} fill="#95A5A6" fillOpacity={0.45} stroke="#BDC3C7" strokeWidth={2} />
                  <ellipse cx={cx + 55} cy={126} rx={18 + 6 * frontFrac} ry={34} fill="#2E8B57" fillOpacity={0.85} />
                </g>
              );
            })}
            <text x={20} y={204} fill="#9FB0C0" fontSize={19}>
              Упругоёмкость β* = m·βж + βс · На Тенгизе упругий режим даёт до 20% запасов
            </text>
          </g>
        ) : null}

        {/* 2. График p(r, t) по формуле -Ei(-x) */}
        <g opacity={panelOpacity('ei')}>
          <rect x={rightX} y={plotTop} width={rightW} height={plotH} rx={14} fill="#121820" stroke="#2D3B49" strokeWidth={1.5} />
          <text x={rightX + 20} y={plotTop + 34} fill="#FFFFFF" fontSize={23} fontWeight={700}>
            Воронка давления p(r, t) по -Ei(-r²/4κt)
          </text>
          <line x1={rightX + 60} y1={plotTop + 56} x2={rightX + rightW - 24} y2={plotTop + 56} stroke="#7A8A9A" strokeDasharray="6,6" />
          <text x={rightX + rightW - 24} y={plotTop + 46} textAnchor="end" fill="#9FB0C0" fontSize={17}>
            pк = 25 МПа
          </text>
          {profiles.map((pr, idx) => {
            const curve: {r_m: number; p_MPa: number}[] = pr.curve || [];
            const pts = curve
              .map((pt) => {
                const px = rightX + 60 + (Math.min(150, pt.r_m) / 150) * (rightW - 90);
                const py = plotTop + plotH - 36 - ((pt.p_MPa - 12) / (25.5 - 12)) * (plotH - 96);
                return `${px.toFixed(1)},${py.toFixed(1)}`;
              })
              .join(' ');
            const rv = profileReveal(idx);
            if (rv <= 0) return null;
            return (
              <polyline
                key={pr.t_sec}
                fill="none"
                stroke={profColors[idx % profColors.length]}
                strokeWidth={3.5}
                points={pts}
                pathLength={1}
                strokeDasharray="1 1"
                strokeDashoffset={1 - rv}
                opacity={interpolate(rv, [0, 0.3, 1], [0, 1, 1])}
              />
            );
          })}
          <text x={rightX + rightW - 24} y={plotTop + plotH - 12} textAnchor="end" fill="#9FB0C0" fontSize={18}>
            Расстояние от скважины r, м (0 → 150)
          </text>
        </g>

        {/* 3. График R(t) = sqrt(r_c^2 + 4*kappa*t) (ПССС) */}
        <g opacity={panelOpacity('rt')}>
          <rect x={rightX} y={rPlotTop} width={rightW} height={rPlotH} rx={14} fill="#121820" stroke="#2D3B49" strokeWidth={1.5} />
          <text x={rightX + 20} y={rPlotTop + 34} fill="#FFFFFF" fontSize={23} fontWeight={700}>
            Радиус возмущения R(t) ∝ √t (не скорость звука!)
          </text>
          {/* Оси графика */}
          <polyline
            fill="none"
            stroke="#4C5E70"
            strokeWidth={1.5}
            points={`${rightX + 60},${rPlotTop + 50} ${rightX + 60},${rPlotTop + rPlotH - 38} ${rightX + rightW - 36},${rPlotTop + rPlotH - 38}`}
          />
          {/* Кривая √t (прорисовка в seg 6, без бегущей точки) */}
          <polyline
            fill="none"
            stroke="#F1C40F"
            strokeWidth={4}
            pathLength={1}
            strokeDasharray="1 1"
            strokeDashoffset={1 - sqrtDraw}
            opacity={sqrtDraw > 0 ? 1 : 0}
            points={Array.from({length: 25}, (_, i) => {
              const frac = i / 24;
              const px = rightX + 60 + frac * (rightW - 96);
              const py = rPlotTop + rPlotH - 38 - Math.sqrt(frac) * (rPlotH - 88);
              return `${px.toFixed(1)},${py.toFixed(1)}`;
            }).join(' ')}
          />
          {showDoubleAnswer ? (
            <g opacity={answerT}>
              <circle cx={rightX + 60 + 0.25 * (rightW - 96)} cy={rPlotTop + rPlotH - 38 - 0.5 * (rPlotH - 88)} r={9} fill="#00CEC9" />
              <text x={rightX + 60 + 0.25 * (rightW - 96) + 12} y={rPlotTop + rPlotH - 38 - 0.5 * (rPlotH - 88) + 22} fill="#00CEC9" fontSize={20} fontWeight={700}>
                1 ч → 100 м
              </text>
              <circle cx={rightX + 60 + 1.0 * (rightW - 96)} cy={rPlotTop + rPlotH - 38 - 1.0 * (rPlotH - 88)} r={9} fill="#2ECC71" />
              <text x={rightX + rightW - 36} y={rPlotTop + 108} textAnchor="end" fill="#2ECC71" fontSize={21} fontWeight={800}>
                4 ч (+3 ч) → 200 м!
              </text>
            </g>
          ) : null}
          <text x={rightX + rightW - 24} y={rPlotTop + rPlotH - 12} textAnchor="end" fill="#9FB0C0" fontSize={18}>
            {showDoubleAnswer ? 'Время t: удвоение радиуса требует ×4 времени' : 'Время t (зависимость радиуса воронки от времени)'}
          </text>
        </g>
      </svg>
    </FrameChrome>
  );
};
