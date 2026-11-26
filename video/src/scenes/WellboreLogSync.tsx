import React from 'react';
import {interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import {VideoProps} from '../types';
import {FrameChrome, getCurrentState} from '../components/FrameChrome';

interface ProfileRow {
  depth_m: number;
  lithology: string;
  fluid: string;
  GR: number;
  RES: number;
  kp_pct: number;
}

export const WellboreLogSync: React.FC<{props: VideoProps}> = ({props}) => {
  const frame = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const t = frame / fps;
  const isPortrait = height > width;
  const {seg, inPause} = getCurrentState(t, props.timings.segments, props.passport.frames || []);
  const segIdx = seg?.index ?? 0;

  const state = props.scene.state_sample || {};
  const rows: ProfileRow[] = state.profile_rows || [];
  const volveIntervals: any[] = state.volve_intervals || [];
  const sg = props.styleguide;

  // Сегмент 3: истинный масштаб 1:1 ("схлопывание в лист бумаги")
  const seg3 = props.timings.segments[3];
  const inTrueScale = segIdx === 3;
  const collapseT = seg3
    ? segIdx < 3
      ? 0
      : segIdx > 3
      ? 0
      : interpolate(t, [seg3.t_start, seg3.t_start + 0.5, seg3.t_end - 0.5, seg3.t_end], [0, 1, 1, 0], {
          extrapolateLeft: 'clamp',
          extrapolateRight: 'clamp',
        })
    : 0;

  const scaleFactor = Math.round(interpolate(collapseT, [0, 1], [25, 1]));
  const scaleText = inTrueScale ? 'Истинный масштаб 1:1' : `Преувеличение ×${scaleFactor} (не в масштабе)`;

  // Прогресс зонда ГИС вдоль глубин 2190..2230 м в первых 3 сегментах
  const seg2End = props.timings.segments[2]?.t_end || 20;
  const toolFrac = Math.min(1, Math.max(0, t / Math.max(1, seg2End)));
  const currentDepth = rows.length
    ? rows[0].depth_m + toolFrac * (rows[rows.length - 1].depth_m - rows[0].depth_m)
    : 2210;

  // Режимы отображения по смысловому сегменту (audio-first)
  const showCrossplot = segIdx === 4 || segIdx === 5;
  const showVolve = segIdx >= 6 && volveIntervals.length > 0;
  const provenanceOverride = showVolve ? 'реальные данные' : showCrossplot ? 'схема' : 'симуляция';

  const panelW = isPortrait ? 952 : 1780;
  const panelH = isPortrait ? 1110 : 710;

  if (showVolve) {
    // seg 6: карточки A → B → C по ходу реплики, кривые прорисовываются; seg 7: фокус на ловушке B и PHIF
    const clampV = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;
    const s6 = props.timings.segments[6];
    const s6Start = s6?.t_start ?? 0;
    const s6Dur = s6 ? s6.t_end - s6.t_start : 6;
    const cardStart = [s6Start, s6Start + 0.25 * s6Dur, s6Start + 0.5 * s6Dur];
    const inTransfer = segIdx >= 7;
    const s7Start = props.timings.segments[7]?.t_start ?? 0;
    const focusT = inTransfer ? interpolate(t, [s7Start, s7Start + 0.6], [0, 1], clampV) : 0;
    const newestCard = cardStart.reduce((acc, ts, i) => (t >= ts ? i : acc), 0);
    return (
      <FrameChrome
        props={props}
        scaleText="Реальный каротаж Volve (глубина вниз)"
        isTrueScale={false}
        provenanceOverride="реальные данные"
        schoolBadge="Западная школа (GR / RT / PHIF)"
      >
        <svg viewBox={`0 0 ${panelW} ${panelH}`} width="100%" height="100%">
          <rect x={0} y={0} width={panelW} height={panelH} rx={20} fill={sg.panel || '#1A2129'} stroke="#2E3B49" strokeWidth={2} />
          <text x={32} y={48} fill="#FFFFFF" fontSize={isPortrait ? 24 : 30} fontWeight={700}>
            Equinor Volve 15/9-F-1 B: сдвиг породы ломает одно RT
          </text>
          <text x={32} y={82} fill="#9FB0C0" fontSize={21}>
            Глубина MD RKB растёт вниз · Сплошная — GR/RT · Пунктир — PHIF/SW
          </text>
          {volveIntervals.slice(0, 3).map((iv, idx) => {
            const cols = isPortrait ? 1 : 3;
            const boxW = isPortrait ? panelW - 64 : (panelW - 96 - 48) / 3;
            const boxH = isPortrait ? 310 : panelH - 130;
            const bx = isPortrait ? 32 : 32 + idx * (boxW + 24);
            const by = isPortrait ? 105 + idx * (boxH + 18) : 108;
            const pts: any[] = iv.points || [];
            const d0 = pts[0]?.depth_m ?? 0;
            const d1 = pts[pts.length - 1]?.depth_m ?? 1;
            const s = iv.summary || {};
            const isTrap = iv.id === 'B';
            const verdict =
              iv.id === 'A' ? 'Нефтяной коллектор' : iv.id === 'B' ? 'Плотный неколлектор (ловушка RT!)' : 'Водоносный песок';
            const borderCol = isTrap ? '#F1C40F' : iv.id === 'A' ? '#2ECC71' : '#3498DB';
            const ts = cardStart[idx] ?? s6Start;
            const appear = inTransfer ? 1 : interpolate(t, [ts, ts + 0.5], [0, 1], clampV);
            const draw = inTransfer ? 1 : interpolate(t, [ts + 0.2, ts + 1.6], [0, 1], clampV);
            const isNewest = !inTransfer && idx === newestCard;
            const cardOpacity = inTransfer ? (isTrap ? 1 : 1 - 0.45 * focusT) : appear;
            const strokeW = inTransfer && isTrap ? 3 + 3 * focusT : isNewest ? 3 : isTrap ? 2.5 : 1.5;
            const phifHi = inTransfer && isTrap;

            const trackTop = by + 94;
            const trackH = boxH - 150;
            const grPts = pts
              .filter((p) => p.GR != null)
              .map((p) => `${bx + 24 + Math.min(1, p.GR / 100) * (boxW - 48)},${trackTop + ((p.depth_m - d0) / Math.max(0.1, d1 - d0)) * trackH}`)
              .join(' ');
            const rtPts = pts
              .filter((p) => p.RT != null)
              .map((p) => {
                const lv = Math.min(1, Math.max(0, (Math.log10(Math.max(0.2, p.RT)) - Math.log10(0.2)) / (Math.log10(200) - Math.log10(0.2))));
                return `${bx + 24 + lv * (boxW - 48)},${trackTop + ((p.depth_m - d0) / Math.max(0.1, d1 - d0)) * trackH}`;
              })
              .join(' ');
            const phifPts = pts
              .filter((p) => p.PHIF != null)
              .map((p) => `${bx + 24 + Math.min(1, p.PHIF / 0.35) * (boxW - 48)},${trackTop + ((p.depth_m - d0) / Math.max(0.1, d1 - d0)) * trackH}`)
              .join(' ');

            return (
              <g key={iv.id} opacity={cardOpacity} transform={`translate(0, ${(1 - appear) * 24})`}>
                {inTransfer && isTrap ? (
                  <rect x={bx - 6} y={by - 6} width={boxW + 12} height={boxH + 12} rx={18} fill="none" stroke="#F1C40F" strokeWidth={8} strokeOpacity={0.3 * focusT} />
                ) : null}
                <rect x={bx} y={by} width={boxW} height={boxH} rx={14} fill="#121820" stroke={borderCol} strokeWidth={strokeW} />
                <text x={bx + 20} y={by + 36} fill="#FFFFFF" fontSize={26} fontWeight={700}>
                  {iv.label}
                </text>
                <text x={bx + 20} y={by + 68} fill={borderCol} fontSize={21} fontWeight={700}>
                  {verdict}
                </text>
                <rect x={bx + 20} y={trackTop} width={boxW - 40} height={trackH} rx={8} fill="#19222C" stroke="#2D3B49" />
                <polyline fill="none" stroke="#2ECC71" strokeWidth={3} points={grPts} pathLength={1} strokeDasharray="1 1" strokeDashoffset={1 - draw} />
                <polyline fill="none" stroke="#F1C40F" strokeWidth={3.5} points={rtPts} pathLength={1} strokeDasharray="1 1" strokeDashoffset={1 - draw} />
                <polyline
                  fill="none"
                  stroke={phifHi ? '#5DADE2' : '#3498DB'}
                  strokeWidth={phifHi ? 3 + 2 * focusT : 3}
                  strokeDasharray="8,5"
                  points={phifPts}
                  opacity={draw}
                />
                <text x={bx + 20} y={by + boxH - 22} fill="#ECEFF4" fontSize={20} fontWeight={600}>
                  RT={s.median_RT} Ом·м ·{' '}
                  <tspan
                    fill={phifHi ? '#5DADE2' : '#ECEFF4'}
                    fontWeight={phifHi ? 800 : 600}
                    fontSize={phifHi ? 23 : 20}
                    textDecoration={phifHi ? 'underline' : undefined}
                  >
                    PHIF={s.median_PHIF}
                  </tspan>{' '}
                  · SW={s.median_SW}
                </text>
              </g>
            );
          })}
        </svg>
      </FrameChrome>
    );
  }

  if (showCrossplot) {
    // Перерисовка палетки рис. 69, стр. 98 (log-log kп 13.8..30.4%, ρп 1..50 Ом·м)
    const cx0 = isPortrait ? 130 : 200;
    const cy0 = isPortrait ? 120 : 80;
    const cw = isPortrait ? 760 : 1080;
    const ch = isPortrait ? 780 : 540;
    const mapX = (kp: number) => cx0 + ((Math.log10(kp) - Math.log10(13.8)) / (Math.log10(30.4) - Math.log10(13.8))) * cw;
    const mapY = (rho: number) => cy0 + ch - ((Math.log10(rho) - Math.log10(1)) / (Math.log10(50) - Math.log10(1))) * ch;

    const polyFromPts = (pts: [number, number][]) => pts.map(([x, y]) => `${mapX(x).toFixed(1)},${mapY(y).toFixed(1)}`).join(' ');
    const oilUpper: [number, number][] = [[17.06, 9.5], [18.7, 11.2], [22, 17.1], [27.6, 31.6], [30.1, 39.7]];
    const oilLower: [number, number][] = [[30.1, 21.2], [25.1, 13.3], [19.9, 8.2], [17.06, 7.3]];
    const watUpper: [number, number][] = [[17.06, 6.6], [22.4, 4.15], [30.3, 2.83]];
    const watLower: [number, number][] = [[30.0, 1.0], [25.0, 1.85], [20.0, 3.92], [17.06, 6.6]];

    const revealAnswer = segIdx >= 5 && !inPause;
    // Последовательное появление точек примера 47 по ходу реплики (координаты палетки не меняются)
    const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;
    const s4 = props.timings.segments[4];
    const s5 = props.timings.segments[5];
    const fadeAt = (t0: number, dur = 0.5) => interpolate(t, [t0, t0 + dur], [0, 1], clamp);
    const s4Dur = s4 ? s4.t_end - s4.t_start : 1;
    const s5Dur = s5 ? s5.t_end - s5.t_start : 1;
    const opA = s4 ? fadeAt(s4.t_start + 1.5, 0.7) : 1;
    const opB = s4 ? fadeAt(s4.t_start + 0.78 * s4Dur, 0.6) : 1;
    const opC = revealAnswer && s5 ? fadeAt(s5.t_start + 0.33 * s5Dur, 0.4) : 0;
    const pts = [
      {label: '47а: 15% (некол.)', x: 15, y: 10, col: '#95A5A6', op: opA, dx: 4, dy: 34, anchor: 'middle', fs: 18},
      {label: revealAnswer ? '47б: 20% → нефть!' : '47б: 20%, 10 Ом·м → ?', x: 20, y: 10, col: '#F1C40F', op: opB, dx: 12, dy: -22, anchor: 'middle', fs: 20},
      {label: '47в: 25% → нефть + вода', x: 25, y: 10, col: '#3498DB', op: opC, dx: 0, dy: 36, anchor: 'middle', fs: 20},
    ];
    // Пульсирующее кольцо вокруг 47б во время паузы предсказания
    const pulsePhase = inPause ? (t * 1.6) % 1 : 0;
    const pulseR = interpolate(pulsePhase, [0, 1], [16, 34]);
    const pulseOp = interpolate(pulsePhase, [0, 0.15, 1], [0, 0.9, 0]);

    return (
      <FrameChrome
        props={props}
        scaleText="Палетка рис. 69, стр. 98 (без искажения)"
        isTrueScale={false}
        provenanceOverride={provenanceOverride}
        schoolBadge="Русская школа ГИС (ρп, kп)"
      >
        <svg viewBox={`0 0 ${panelW} ${panelH}`} width="100%" height="100%">
          <rect x={0} y={0} width={panelW} height={panelH} rx={20} fill={sg.panel || '#1A2129'} stroke="#2E3B49" strokeWidth={2} />
          <text x={40} y={50} fill="#FFFFFF" fontSize={28} fontWeight={700}>
            Перерисовано по рис. 69, стр. 98: ρп = f(kп) при пороге kп* = 17 %
          </text>
          {/* Область неколлектора */}
          <rect x={mapX(13.8)} y={cy0} width={mapX(17) - mapX(13.8)} height={ch} fill="#8C7B6B" fillOpacity={0.32} />
          <text x={(mapX(13.8) + mapX(17)) / 2} y={cy0 + 48} textAnchor="middle" fill="#ECEFF4" fontSize={20} fontWeight={700}>
            Неколлектор
          </text>
          {/* Зона I: нефть */}
          <polygon points={polyFromPts([...oilUpper, ...oilLower])} fill="#2E8B57" fillOpacity={0.42} stroke="#2ECC71" strokeWidth={2} />
          <text x={mapX(25.5)} y={mapY(21)} fill="#82E0AA" fontSize={22} fontWeight={700}>
            I: нефть
          </text>
          {/* Зона II: нефть с водой */}
          <polygon
            points={polyFromPts([[17.06, 7.3], [22.4, 10.2], [30.3, 21.2], [30.3, 2.83], [22.4, 4.15], [17.06, 6.6]])}
            fill="#F1C40F"
            fillOpacity={0.22}
            stroke="#F1C40F"
            strokeWidth={2}
          />
          <text x={mapX(24.5)} y={mapY(6.2)} fill="#F7DC6F" fontSize={22} fontWeight={700}>
            II: нефть + вода
          </text>
          {/* Зона III: вода */}
          <polygon points={polyFromPts([...watUpper, ...watLower])} fill="#3498DB" fillOpacity={0.32} stroke="#5DADE2" strokeWidth={2} />
          <text x={mapX(25)} y={mapY(2.0)} fill="#85C1E9" fontSize={22} fontWeight={700}>
            III: вода
          </text>

          {/* Порог kп* = 17% и уровень ρп = 10 Ом·м */}
          <line x1={mapX(17)} y1={cy0} x2={mapX(17)} y2={cy0 + ch} stroke="#ECEFF4" strokeWidth={3} strokeDasharray="10,6" />
          <text x={mapX(17) + 10} y={cy0 + ch - 18} fill="#FFFFFF" fontSize={21} fontWeight={700}>
            kп* = 17 %
          </text>
          <line x1={cx0} y1={mapY(10)} x2={cx0 + cw} y2={mapY(10)} stroke="#F1C40F" strokeWidth={2} strokeDasharray="6,6" />

          {/* Оси */}
          <rect x={cx0} y={cy0} width={cw} height={ch} fill="none" stroke="#7A8A9A" strokeWidth={2} />
          {[15, 17, 20, 25, 30].map((v) => (
            <text key={v} x={mapX(v)} y={cy0 + ch + 34} textAnchor="middle" fill="#AEB9C5" fontSize={21}>
              {v}%
            </text>
          ))}
          {[1, 2, 5, 10, 20, 50].map((v) => (
            <text key={v} x={cx0 - 16} y={mapY(v) + 7} textAnchor="end" fill="#AEB9C5" fontSize={21}>
              {v}
            </text>
          ))}
          <text x={cx0 + cw / 2} y={cy0 + ch + 72} textAnchor="middle" fill="#FFFFFF" fontSize={24} fontWeight={700}>
            Пористость kп, % (лог. шкала)
          </text>
          <text x={44} y={cy0 + ch / 2} transform={`rotate(-90 44 ${cy0 + ch / 2})`} textAnchor="middle" fill="#FFFFFF" fontSize={24} fontWeight={700}>
            Сопротивление ρп, Ом·м
          </text>

          {/* Точки примера 47 */}
          {inPause ? (
            <circle cx={mapX(20)} cy={mapY(10)} r={pulseR} fill="none" stroke="#F1C40F" strokeWidth={4} strokeOpacity={pulseOp} />
          ) : null}
          {pts
            .filter((p) => p.op > 0)
            .map((p) => (
              <g key={p.label} opacity={p.op}>
                <circle cx={mapX(p.x)} cy={mapY(p.y)} r={12} fill={p.col} stroke="#0F1419" strokeWidth={3} />
                <text
                  x={mapX(p.x) + p.dx}
                  y={mapY(p.y) + p.dy}
                  textAnchor={p.anchor as 'start' | 'middle' | 'end'}
                  fill="#FFFFFF"
                  fontSize={p.fs}
                  fontWeight={700}
                  paintOrder="stroke"
                  stroke="#0F1419"
                  strokeWidth={5}
                  strokeLinejoin="round"
                >
                  {p.label}
                </text>
              </g>
            ))}
        </svg>
      </FrameChrome>
    );
  }

  // Сегменты 0..3: разрез скважины + движущийся зонд ГИС + синхронные кривые (и схлопывание в 1:1 на seg 3)
  const colW = isPortrait ? 310 : 520;
  const colX = 48;
  const trackX = colX + colW + 44;
  const trackW = (panelW - trackX - 48 - 32) / 3;
  const topY = 82;
  const fullLogH = panelH - 150;
  const drawH = interpolate(collapseT, [0, 1], [fullLogH, 86]);
  const drawTop = topY + (fullLogH - drawH) / 2;

  const dMin = rows[0]?.depth_m ?? 2190;
  const dMax = rows[rows.length - 1]?.depth_m ?? 2230;
  const yOfDepth = (z: number) => drawTop + ((z - dMin) / Math.max(1, dMax - dMin)) * drawH;

  const boreX = colX + 26;
  const layers = [
    {z0: 2190, z1: 2202, title: 'Глина', sub: '(вода)', color: sg.lithology?.shale?.color || '#6B8E5A'},
    {z0: 2202, z1: 2214, title: 'Плотный известняк', sub: '(пористость 4%)', color: sg.lithology?.dense?.color || '#8C7B6B'},
    {z0: 2214, z1: 2230, title: 'Песчаник с нефтью', sub: '(пористость 24%)', color: sg.lithology?.sand?.color || '#E8C872'},
  ];

  const polyCurve = (tx: number, w: number, fn: (r: ProfileRow) => number) =>
    rows.map((r) => `${(tx + 12 + fn(r) * (w - 24)).toFixed(1)},${yOfDepth(r.depth_m).toFixed(1)}`).join(' ');

  const toolY = yOfDepth(currentDepth);

  return (
    <FrameChrome
      props={props}
      scaleText={scaleText}
      isTrueScale={inTrueScale}
      provenanceOverride={provenanceOverride}
      schoolBadge="Русская школа ГИС (ГК, КС, kп)"
    >
      <svg viewBox={`0 0 ${panelW} ${panelH}`} width="100%" height="100%">
        <rect x={0} y={0} width={panelW} height={panelH} rx={20} fill={sg.panel || '#1A2129'} stroke="#2E3B49" strokeWidth={2} />
        <text x={colX} y={48} fill="#FFFFFF" fontSize={25} fontWeight={700}>
          Разрез (глубина ↓)
        </text>
        <text x={trackX + trackW / 2} y={48} textAnchor="middle" fill="#2ECC71" fontSize={24} fontWeight={700}>
          ГК (API)
        </text>
        <text x={trackX + trackW + 16 + trackW / 2} y={48} textAnchor="middle" fill="#F1C40F" fontSize={24} fontWeight={700}>
          КС (Ом·м)
        </text>
        <text x={trackX + 2 * (trackW + 16) + trackW / 2} y={48} textAnchor="middle" fill="#3498DB" fontSize={24} fontWeight={700}>
          Пористость kп (%)
        </text>

        {/* Слои разреза */}
        {layers.map((lay) => {
          const ly0 = yOfDepth(lay.z0);
          const ly1 = yOfDepth(lay.z1);
          const midY = (ly0 + ly1) / 2;
          return (
            <g key={lay.title}>
              <rect x={colX} y={ly0} width={colW} height={Math.max(4, ly1 - ly0)} fill={lay.color} fillOpacity={0.85} stroke="#0F1419" strokeWidth={2} />
              {!inTrueScale ? (
                <text x={boreX + 32} y={midY - 6} fill="#0F1419" fontSize={isPortrait ? 19 : 21} fontWeight={800}>
                  <tspan x={boreX + 32} dy={0}>{lay.title}</tspan>
                  <tspan x={boreX + 32} dy={24} fontWeight={700}>{lay.sub}</tspan>
                </text>
              ) : null}
            </g>
          );
        })}

        {/* Ствол скважины и зонд */}
        <rect x={boreX} y={drawTop} width={20} height={drawH} fill="#1E2730" stroke="#7A8A9A" strokeWidth={1.5} />
        <rect x={boreX + 2} y={toolY - 16} width={16} height={32} rx={4} fill="#F1C40F" stroke="#0F1419" strokeWidth={2} />

        {/* Треки каротажа (вычислены из одной модели глубины z) */}
        {[
          {idx: 0, col: '#2ECC71', dash: '', fn: (r: ProfileRow) => r.GR / 100},
          {idx: 1, col: '#F1C40F', dash: '', fn: (r: ProfileRow) => r.RES / 35},
          {idx: 2, col: '#3498DB', dash: '8,5', fn: (r: ProfileRow) => r.kp_pct / 30},
        ].map((tr) => {
          const tx = trackX + tr.idx * (trackW + 16);
          return (
            <g key={tr.idx}>
              <rect x={tx} y={drawTop} width={trackW} height={drawH} rx={8} fill="#121820" stroke="#2D3B49" strokeWidth={1.5} />
              <polyline fill="none" stroke={tr.col} strokeWidth={4} strokeDasharray={tr.dash} points={polyCurve(tx, trackW, tr.fn)} />
            </g>
          );
        })}

        {/* Горизонтальная линия синхронизации зонда и кривых */}
        <line x1={colX} y1={toolY} x2={panelW - 40} y2={toolY} stroke="#FFFFFF" strokeWidth={2} strokeDasharray="6,4" strokeOpacity={0.75} />

        {inTrueScale ? (
          <text x={panelW / 2} y={drawTop + drawH + 56} textAnchor="middle" fill="#2ECC71" fontSize={26} fontWeight={700}>
            Масштаб 1:1 — пласт 10 м тоньше миллиметра на фоне длины скважины
          </text>
        ) : (
          <text x={colX} y={panelH - 22} fill="#9FB0C0" fontSize={21}>
            Плотный известняк (2202–2214 м) и нефтяной песчаник (2214–2230 м) оба дают пик КС
          </text>
        )}
      </svg>
    </FrameChrome>
  );
};
