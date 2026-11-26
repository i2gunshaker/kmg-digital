import React from 'react';
import {useCurrentFrame, useVideoConfig} from 'remotion';
import {TimingSegment, VideoProps} from '../types';
import {FrameChrome, getCurrentState} from '../components/FrameChrome';
import {
  Diagram,
  Label,
  Line,
  cue,
  ease,
  fraction,
  amber,
  blue,
  grain,
  grid,
  ink,
  mint,
  muted,
  oil,
  panel,
  water,
} from '../components/TrainingDiagram';

type Row = {depth_m: number; RES: number; kp_pct: number};
type Interval = {
  id: string;
  depth_range_m: number[];
  summary: {median_PHIF: number; median_RT: number; median_SW: number};
};
const layers = [
  {top: 2190, base: 2202, name: ['Глина'], color: '#97b4a2'},
  {top: 2202, base: 2214, name: ['Плотный', 'камень'], color: '#b8b2aa'},
  {top: 2214, base: 2230, name: ['Песчаник', 'с нефтью'], color: grain},
];

function PorePair({time}: {time: number}) {
  return (
    <>
      <Label x={48} y={88} size={44} bold>
        Между зёрнами песчаника
      </Label>
      {[false, true].map((o, i) => (
        <g key={i} transform={`translate(${38 + i * 450} 184)`}>
          <Label x={208} y={46} size={38} anchor="middle" color={o ? mint : blue}>
            {o ? 'Нефть в порах' : 'Солёная вода'}
          </Label>
          <svg y="95" width="416" height="425" viewBox="0 0 416 425" overflow="hidden">
            <rect width="416" height="425" fill={water} />
            {o && <path d="M200 0 H416 V425 H220 Q165 295 220 225 Q255 150 200 0" fill={oil} />}
            {[
              [-8, 25, 86],
              [155, 37, 83],
              [318, 24, 80],
              [68, 156, 56],
              [230, 142, 64],
              [390, 165, 73],
              [-5, 300, 76],
              [154, 315, 77],
              [320, 311, 77],
              [58, 439, 54],
              [230, 439, 66],
              [405, 440, 69],
            ].map(([x, y, r], j) => (
              <circle key={j} cx={x} cy={y} r={r} fill={grain} stroke="#826f57" strokeWidth="5" />
            ))}
            <path
              d={o ? 'M0 212 Q65 239 121 219 T193 218' : 'M0 212 Q65 239 121 219 T221 216 T320 220 T416 241'}
              fill="none"
              stroke="#17394a"
              strokeWidth="12"
              strokeDasharray="19 13"
              strokeDashoffset={-time * 68}
            />
            {o && <path d="M186 202l28 30m0-30l-28 30" stroke="#4c3321" strokeWidth="10" />}
          </svg>
          <Label x={208} y={599} size={39} anchor="middle" bold>
            {o ? 'Току трудно' : 'Ток проходит'}
          </Label>
        </g>
      ))}
      <Line x1={50} x2={900} y1={865} y2={865} />
      <Label x={48} y={945} color={muted}>
        Пунктир показывает путь тока
      </Label>
    </>
  );
}

function LogPanel({rows, second, t}: {rows: Row[]; second: boolean; t: number}) {
  const y = (z: number) => 205 + (z - 2190) * 13;
  const current = 2190 + 40 * fraction(t / (second ? 8 : 7));
  const tracks = [
    {x: 408, key: 'RES' as const, max: 35, color: '#f1c40f'},
    ...(second ? [{x: 685, key: 'kp_pct' as const, max: 30, color: blue}] : []),
  ];
  const zoom = second ? 1 : 1 + 0.12 * (1 - ease(t));
  return (
    <g transform={`translate(476 500) scale(${zoom}) translate(-476 -500)`}>
      <Label x={42} y={78} size={36} bold>
        Глубина ↓
      </Label>
      <Label x={408} y={78} size={32} color={amber}>
        Сопротивление
      </Label>
      <Label x={408} y={126} size={32} color={amber}>
        КС, Ом·м
      </Label>
      {second && (
        <>
          <Label x={685} y={78} size={32} color={blue}>
            Пустоты
          </Label>
          <Label x={685} y={126} size={30} color={blue}>
            Пористость kп, %
          </Label>
        </>
      )}
      {layers.map((l, i) => (
        <g key={l.top}>
          <rect x="45" y={y(l.top)} width="320" height={y(l.base) - y(l.top)} fill={l.color} />
          <rect
            x="45"
            y={y(l.top)}
            width="320"
            height={y(l.base) - y(l.top)}
            fill={i === 0 ? 'url(#hatch)' : i === 2 ? 'url(#dots)' : 'none'}
          />
          {l.name.map((name, j) => (
            <Label
              key={j}
              x={111}
              y={(y(l.top) + y(l.base)) / 2 + j * 40 - (l.name.length - 1) * 12}
              size={35}
              color="#15252a"
              bold
            >
              {name}
            </Label>
          ))}
        </g>
      ))}
      <Line x1={76} x2={76} y1={205} y2={725} weight={13} color="#304650" />
      {tracks.map((tr) => (
        <g key={tr.key}>
          <rect x={tr.x} y={205} width={210} height={520} fill="#101e26" stroke={grid} />
          <Label x={tr.x} y={184} size={29} color={muted}>
            0
          </Label>
          <Label x={tr.x + 210} y={184} size={29} color={muted} anchor="end">
            {tr.max}
          </Label>
          <polyline
            points={rows.map((r) => `${tr.x + (r[tr.key] / tr.max) * 210},${y(r.depth_m)}`).join(' ')}
            fill="none"
            stroke={tr.color}
            strokeWidth="7"
            strokeDasharray={tr.key === 'RES' ? undefined : '15 9'}
          />
        </g>
      ))}
      <Line x1={372} x2={second ? 910 : 628} y1={y(current)} y2={y(current)} color={ink} dash="8 7" />
      <Line x1={78} x2={98} y1={y(current)} y2={y(current)} color={ink} />
      <rect x={63} y={y(current) - 14} width={26} height={28} rx={6} fill="#fff" stroke="#25414b" strokeWidth={4} />
      {tracks.map((tr) => {
        const i = Math.min(rows.length - 2, Math.floor((current - 2190) / 2));
        const value = rows[i][tr.key] + ((rows[i + 1][tr.key] - rows[i][tr.key]) * (current - rows[i].depth_m)) / 2;
        return (
          <circle
            key={tr.key}
            cx={tr.x + (value / tr.max) * 210}
            cy={y(current)}
            r="10"
            fill={ink}
            stroke={tr.color}
            strokeWidth="4"
          />
        );
      })}
      {second ? (
        [4, 24].map((n, i) => (
          <g key={n} transform={`translate(${48 + 450 * i} 815)`}>
            <rect width="400" height="188" rx="14" fill="#243a43" />
            <rect x={20} y={36} width={95} height={95} rx="5" fill={grain} />
            {[0, 1, 2, 3].map((k) => (
              <circle
                key={k}
                cx={44 + (k % 2) * 43}
                cy={61 + Math.floor(k / 2) * 43}
                r={Math.sqrt(((n / 100) * 95 ** 2) / (4 * Math.PI))}
                fill={i ? oil : '#555c60'}
                stroke="#485354"
                strokeWidth="2"
              />
            ))}
            <Label x={137} y={81} size={51} bold>
              {n}%
            </Label>
            <Label x={137} y={126} size={30} color={muted}>
              пор в модели
            </Label>
            <Label x={20} y={168} size={29}>
              {i ? 'Песчаник с нефтью' : 'Плотный камень'}
            </Label>
          </g>
        ))
      ) : (
        <>
          <Label x={655} y={440} size={31} color={amber}>
            28 · высокое
          </Label>
          <Label x={655} y={620} size={31} color={amber}>
            24 · высокое
          </Label>
          <Label x={47} y={868} size={43} bold>
            Току трудно в обоих слоях
          </Label>
          <Label x={47} y={936} size={36} color={muted}>
            Два уровня высокого сопротивления
          </Label>
        </>
      )}
    </g>
  );
}

function TrueSection({factor}: {factor: number}) {
  const px = 0.82;
  const y = (z: number) => 505 + (z - 2210) * px * factor;
  return (
    <>
      <Label x={48} y={75} size={38}>
        Тот же разрез · глубина ↓
      </Label>
      {layers.map((l) => (
        <rect key={l.top} x="68" y={y(l.top)} width={820} height={y(l.base) - y(l.top)} fill={l.color} />
      ))}
      <Line x1={68} x2={888} y1={963} y2={963} color={ink} />
      {[68, 888].map((x) => (
        <Line key={x} x1={x} x2={x} y1={951} y2={975} color={ink} />
      ))}
      <Label x={478} y={1029} anchor="middle">
        1000 м
      </Label>
      <path d={`M58 ${y(2190)}h-14 V${y(2230)}h14`} fill="none" stroke={ink} strokeWidth="4" />
      <Label x={70} y={Math.min(940, y(2230) + 46)} size={32}>
        40 м по вертикали
      </Label>
    </>
  );
}

type Point = [number, number];
function Palette({seg, time}: {seg: TimingSegment; time: number}) {
  const x = (v: number) => 142 + (750 * Math.log(v / 13.8)) / Math.log(30.4 / 13.8);
  const y = (v: number) => 747 - (535 * Math.log(v)) / Math.log(50);
  const pts = (values: Point[]) => values.map(([a, b]) => `${x(a)},${y(b)}`).join(' ');
  const bands: Point[][] = [
    [
      [17.06, 9.5],
      [18.7, 11.2],
      [22, 17.1],
      [27.6, 31.6],
      [30.1, 39.7],
      [30.1, 21.2],
      [25.1, 13.3],
      [19.9, 8.2],
      [17.06, 7.3],
    ],
    [
      [17.06, 7.3],
      [19.9, 8.2],
      [25.1, 13.3],
      [30.1, 21.2],
      [30.3, 2.83],
      [22.4, 4.15],
      [17.06, 6.6],
    ],
    [
      [17.06, 6.6],
      [22.4, 4.15],
      [30.3, 2.83],
      [30, 1],
      [25, 1.85],
      [20, 3.92],
    ],
  ];
  const answering = seg.index === 5;
  const foundOil = answering && time >= cue(seg, 'нефть');
  const shift = answering ? ease((time - cue(seg, 'двадцати пяти')) / 0.5) : 0;
  const mixture = answering && time >= cue(seg, 'водой');
  const first = answering || time >= cue(seg, 'пятнадцати');
  const question = answering || time >= cue(seg, 'двадцати');
  return (
    <>
      <Label x={48} y={78} size={40} bold>
        График для этого месторождения
      </Label>
      <Label x={48} y={132} size={33} color={muted}>
        Рис. 69 · пример 47 · стр. 97–98
      </Label>
      <rect x={142} y={212} width={750} height={535} fill="#13212b" stroke={grid} />
      <rect x={142} y={212} width={x(17) - 142} height={535} fill="#534c45" />
      {bands.map((b, i) => (
        <g key={i}>
          <polygon points={pts(b)} fill={['#8e6440', '#4d7271', '#335e77'][i]} />
          <polygon points={pts(b)} fill={['url(#dots)', 'url(#hatch)', 'none'][i]} />
        </g>
      ))}
      {[1, 2, 5, 10, 20, 50].map((v) => (
        <g key={v}>
          <Line x1={142} x2={892} y1={y(v)} y2={y(v)} color={v === 10 ? ink : grid} dash={v === 10 ? '10 9' : '3 10'} />
          <Label x={124} y={y(v) + 10} size={30} anchor="end">
            {v}
          </Label>
        </g>
      ))}
      {[15, 17, 20, 25, 30].map((v) => (
        <Label key={v} x={x(v)} y={798} anchor="middle" size={33}>
          {v}
        </Label>
      ))}
      <text transform="translate(48 487) rotate(-90)" textAnchor="middle" fontSize="35" fill={ink}>
        Сопротивление пласта, Ом·м
      </text>
      <Label x={514} y={854} size={38} anchor="middle">
        Пористость, %
      </Label>
      <Line x1={x(17)} x2={x(17)} y1={212} y2={747} color={ink} dash="10 6" />
      <Label x={x(17) + 9} y={256} size={30}>
        17% · граница
      </Label>
      {first && <circle cx={x(15)} cy={y(10)} r={12} fill={ink} />}
      {question && (
        <>
          <circle cx={x(20 + 5 * shift)} cy={y(10)} r={17} fill={amber} stroke="#18232a" strokeWidth={4} />
          <Label x={x(20 + 5 * shift)} y={y(10) - 28} size={37} bold anchor="middle">
            {shift > 0.9 ? '25%' : foundOil ? 'Нефть' : '20% ?'}
          </Label>
        </>
      )}
      {mixture && (
        <>
          <Label x={x(25)} y={y(25)} size={31} anchor="middle">
            Нефтяной пласт
          </Label>
          <Label x={x(24)} y={y(5.9)} size={30} anchor="middle">
            Нефть + вода
          </Label>
          <Label x={x(27)} y={y(1.75)} size={30} anchor="middle">
            Вода
          </Label>
        </>
      )}
      <Label x={48} y={952} size={39} bold>
        {mixture
          ? '25% · нефть с водой'
          : foundOil
            ? '20% · получим нефть'
            : first
              ? '15% · притока не ожидают'
              : 'Сравним точки при 10 Ом·м'}
      </Label>
      {mixture &&
        [oil, water].map((c, i) => (
          <path key={c} transform={`translate(${803 + i * 56} 928)`} d="M0 -25Q-31 12 -10 24Q26 36 17 8Z" fill={c} />
        ))}
    </>
  );
}

function DomainShift({seg, time}: {seg: TimingSegment; time: number}) {
  const change = time >= cue(seg, 'Попался');
  const error = time >= cue(seg, 'сухую');
  return (
    <>
      <Label x={46} y={95} size={43} bold>
        Что выучила модель
      </Label>
      {['Песчаник', 'Известняк'].map((s, i) => (
        <g key={s} opacity={i && !change ? 0 : 1} transform={`translate(${46 + i * 443} 171)`}>
          <rect width={413} height={550} rx={20} fill="#243742" stroke={grid} strokeWidth={2} />
          <Label x={26} y={62} size={32} color={muted}>
            {i ? 'Новая порода' : 'Обучение'}
          </Label>
          <Label x={26} y={139} size={47} bold>
            {s}
          </Label>
          <rect x={28} y={184} width={353} height={134} rx={8} fill={i ? '#b7afa4' : grain} />
          {Array.from({length: 16}, (_, j) => (
            <ellipse
              key={j}
              cx={51 + (j % 8) * 44}
              cy={216 + Math.floor(j / 8) * 64}
              rx={i ? 5 : 16}
              ry={i ? 4 : 12}
              fill={i ? '#52666a' : oil}
            />
          ))}
          <Label x={26} y={388} size={35}>
            Высокое КС
          </Label>
          <Label x={26} y={467} size={43} bold color={amber}>
            → Нефть
          </Label>
          {i === 1 && error && <Line x1={87} x2={218} y1={448} y2={451} color={ink} weight={7} />}
        </g>
      ))}
      {change && (
        <Label x={49} y={819} size={42} color={blue}>
          Сдвиг домена · мало пор (4%)
        </Label>
      )}
      {error && (
        <>
          <Label x={49} y={913} size={46} bold>
            Риск сухой скважины
          </Label>
          <Label x={49} y={979} size={32} color={muted}>
            Условный пример
          </Label>
        </>
      )}
    </>
  );
}

function Volve({intervals, reveal}: {intervals: Interval[]; reveal: boolean}) {
  return (
    <>
      <Label x={44} y={72} size={45} bold>
        Equinor Volve · 15/9-F-1 B
      </Label>
      <Label x={44} y={139} size={32} color={muted}>
        Медианы 50 измерений в каждом интервале
      </Label>
      {intervals.slice(0, 2).map((iv, i) => (
        <g key={iv.id} transform={`translate(43 ${204 + i * 390})`}>
          <rect width="866" height="345" rx="18" fill="#233642" />
          <rect width="10" height="345" rx="5" fill={i ? blue : amber} />
          <Label x={38} y={72} size={45} bold>
            Интервал {iv.id}
          </Label>
          <Label x={826} y={70} size={35} anchor="end" color={muted}>
            {iv.depth_range_m.join('–')} м
          </Label>
          <Label x={38} y={143} size={39}>
            Сопротивление RT · {iv.summary.median_RT} Ом·м
          </Label>
          {reveal && (
            <>
              <Label x={38} y={218} size={41} bold color={i ? blue : amber}>
                Пористость · {(100 * iv.summary.median_PHIF).toFixed(1)}%
              </Label>
              <Label x={38} y={293} size={33}>
                {i ? 'Плотный прослой' : 'Нефтеносный песчаник · SW ' + iv.summary.median_SW}
              </Label>
            </>
          )}
        </g>
      ))}
    </>
  );
}

export const WellboreLogSync: React.FC<{props: VideoProps}> = ({props}) => {
  const time = useCurrentFrame() / useVideoConfig().fps;
  const {seg} = getCurrentState(time, props.timings.segments, props.passport.frames);
  const i = seg.index,
    local = time - seg.t_start;
  const state = props.scene.state_sample;
  const factor = 25 - 24 * ease(local / 2);
  const scale =
    i === 3
      ? factor === 1
        ? '1:1 по обеим осям'
        : `Вертикаль ×${factor.toFixed(1)}`
      : i === 1 || i === 2
        ? 'Вертикаль ×25'
        : undefined;
  let body: React.ReactNode;
  if (i === 0) body = <PorePair time={time} />;
  else if (i <= 2) body = <LogPanel rows={state.profile_rows} second={i === 2} t={local} />;
  else if (i === 3) body = <TrueSection factor={factor} />;
  else if (i <= 5) body = <Palette seg={seg} time={time} />;
  else if (i === 6) body = <DomainShift seg={seg} time={time} />;
  else body = <Volve intervals={state.volve_intervals} reveal={time >= cue(seg, 'места')} />;
  const heading =
    i === 3 && factor > 1
      ? 'Снимаем растяжение'
      : i === 5 && time < cue(seg, 'водой')
        ? 'Что даст этот слой?'
        : undefined;
  return (
    <FrameChrome
      props={props}
      heading={heading}
      scaleText={scale}
      isTrueScale={i === 3 && factor === 1}
      provenanceOverride={i === 7 ? 'реальные данные' : i >= 1 && i <= 3 ? 'симуляция' : 'схема'}
    >
      <Diagram w={952} h={1080}>
        {body}
      </Diagram>
    </FrameChrome>
  );
};
