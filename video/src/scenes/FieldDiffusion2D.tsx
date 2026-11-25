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

        {/* 1. Разрез упругого пласта с покрышкой (глубина вниз) */}
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

        {/* Врезка микропор (×10 000): сжатие скелета + расширение нефти */}
        {!isPortrait ? (
          <g transform={`translate(${leftX}, ${slabTop + slabMaxH + 34})`}>
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
        <g>
          <rect x={rightX} y={plotTop} width={rightW} height={plotH} rx={14} fill="#121820" stroke="#2D3B49" strokeWidth={1.5} />
          <text x={rightX + 20} y={plotTop + 34} fill="#FFFFFF" fontSize={23} fontWeight={700}>
            Воронка давления p(r, t) по -Ei(-r²/4κt)
          </text>
          <line x1={rightX + 60} y1={plotTop + 56} x2={rightX + rightW - 24} y2={plotTop + 56} stroke="#7A8A9A" strokeDasharray="6,6" />
          <text x={rightX + 66} y={plotTop + 50} fill="#9FB0C0" fontSize={17}>
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
            return <polyline key={pr.t_sec} fill="none" stroke={profColors[idx % profColors.length]} strokeWidth={3.5} points={pts} />;
          })}
          <text x={rightX + rightW - 24} y={plotTop + plotH - 12} textAnchor="end" fill="#9FB0C0" fontSize={18}>
            Расстояние от скважины r, м (0 → 150)
          </text>
        </g>

        {/* 3. График R(t) = sqrt(r_c^2 + 4*kappa*t) (ПССС) */}
        <g>
          <rect x={rightX} y={rPlotTop} width={rightW} height={rPlotH} rx={14} fill="#121820" stroke="#2D3B49" strokeWidth={1.5} />
          <text x={rightX + 20} y={rPlotTop + 34} fill="#FFFFFF" fontSize={23} fontWeight={700}>
            Радиус возмущения R(t) ∝ √t (не скорость звука!)
          </text>
          {/* Кривая √t */}
          <polyline
            fill="none"
            stroke="#F1C40F"
            strokeWidth={4}
            points={Array.from({length: 25}, (_, i) => {
              const frac = i / 24;
              const px = rightX + 60 + frac * (rightW - 96);
              const py = rPlotTop + rPlotH - 38 - Math.sqrt(frac) * (rPlotH - 88);
              return `${px.toFixed(1)},${py.toFixed(1)}`;
            }).join(' ')}
          />
          {showDoubleAnswer ? (
            <g>
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
            Время t: удвоение радиуса требует ×4 времени
          </text>
        </g>
      </svg>
    </FrameChrome>
  );
};
