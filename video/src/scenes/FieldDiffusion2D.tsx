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

type Profile = {t_sec: number; R_psss_m: number; curve: {r_m: number; p_MPa: number}[]};
type RadiusTask = {kappa_m2_s: number; curve: {hours: number; radius_m: number}[]};

function Mechanism({seg, t}: {seg: TimingSegment; t: number}) {
  const i = seg.index,
    local = t - seg.t_start;
  const zoom = i === 0 ? 0 : i === 1 ? ease(local / 1.2) : 1 - ease(local / 1.6);
  const w = 500 + 746 * zoom,
    h = w * 0.3,
    x = 1160 - 755 * zoom,
    y = 300 - 118 * zoom;
  const change = i === 1 ? ease((t - cue(seg, 'давление')) / 4) : i === 2 ? 1 : 0;
  const phase = (t * 0.38) % 1;
  return (
    <>
      <Label x={65} y={70} size={39} color={muted}>
        Разрез пласта · глубина ↓
      </Label>
      <rect x={60} y={231} width={1660} height={99} fill="#98ab97" />
      <rect x={60} y={231} width={1660} height={99} fill="url(#hatch)" />
      <rect x={60} y={330} width={1660} height={205} fill={grain} />
      <rect x={60} y={330} width={1660} height={205} fill="url(#dots)" />
      <path d="M220 130V470h36V130" fill="#23323a" stroke={ink} strokeWidth={5} />
      <Line x1={261} x2={282} y1={153} y2={153} color={ink} />
      <Label x={290} y={169} size={37} bold>
        Скважина
      </Label>
      {zoom < 0.2 && (
        <Label x={365} y={295} size={38} color="#14302e">
          Покрышка
        </Label>
      )}
      {change > 0 && (
        <path d={`M${420 + phase * 52} 428h-115m0 0l26-17m-26 17l26 17`} stroke="#4b3b29" strokeWidth={8} fill="none" />
      )}
      <svg x={x} y={y} width={w} height={h} viewBox="0 0 1000 300" overflow="hidden">
        <rect width="1000" height="300" fill={oil} />
        {[0, 1, 2, 3, 4].map((k) => (
          <g key={k}>
            {[false, true].map((bottom) => {
              const cx = 68 + k * 220 + (bottom ? 46 : 0),
                cy = bottom ? 332 : -28;
              return (
                <g key={String(bottom)}>
                  <ellipse
                    cx={cx}
                    cy={cy + (bottom ? -1 : 1) * change * 25}
                    rx={108}
                    ry={129}
                    fill={grain}
                    stroke="#735e48"
                    strokeWidth={4}
                  />
                  {change > 0 && (
                    <ellipse
                      cx={cx}
                      cy={cy}
                      rx={108}
                      ry={129}
                      fill="none"
                      stroke="#584a40"
                      strokeWidth={4}
                      strokeDasharray="10 8"
                    />
                  )}
                </g>
              );
            })}
          </g>
        ))}
        <ellipse
          cx={640 - change * 290}
          cy={153}
          rx={61 + change * 18}
          ry={20 + change * 2}
          fill={mint}
          stroke="#d9fff0"
          strokeWidth={4}
        />
        {i === 1 && <path d="M305 153H120l28 -18m-28 18l28 18" fill="none" stroke={ink} strokeWidth={8} />}
        <rect x="2" y="2" width={996} height="296" rx={6} fill="none" stroke={ink} strokeWidth={5} />
      </svg>
      {i === 0 && (
        <>
          <Label x={420} y={457} color="#332d24" size={50} bold>
            Нефть в порах
          </Label>
          <Label x={65} y={632} size={47} bold>
            В модели 25 МПа ≈ 250 атм
          </Label>
        </>
      )}
      {i === 1 && (
        <>
          <Label x={56} y={609} color={muted} size={34}>
            Поры увеличены, деформация усилена
          </Label>
          <Label x={635} y={662} size={44} bold color={amber}>
            {t >= cue(seg, 'нефть течёт')
              ? '← К скважине'
              : t >= cue(seg, 'стенки')
                ? 'Стенки сближаются'
                : t >= cue(seg, 'чуть')
                  ? 'Нефть расширяется'
                  : 'Снижается давление'}
          </Label>
        </>
      )}
      {i === 2 && (
        <Label x={65} y={636} size={46} bold>
          {t >= cue(seg, 'Тенгизе') ? 'Оценка для Тенгиза · стр. 478' : 'Упругий запас всего пласта'}
        </Label>
      )}
    </>
  );
}

function ScaleSection({factor}: {factor: number}) {
  const w = 1450,
    h = ((w * 20) / 1000) * factor,
    y = 349 - h / 2;
  return (
    <>
      <rect x={115} y={y - 20} width={w} height={20} fill="#97ac9c" />
      <rect x={115} y={y} width={w} height={h} fill={grain} />
      <rect x={115} y={y} width={w} height={h} fill="url(#dots)" />
      <Label x={116} y={36} size={31} color={muted}>
        Глубина ↓
      </Label>
      <path d={`M1581 ${y} h14 v${h} h-14`} fill="none" stroke={ink} strokeWidth="4" />
      <Label x={1614} y={359} size={39}>
        20 м
      </Label>
      <path d="M115 647v13h1450v-13" fill="none" stroke={ink} strokeWidth="4" />
      <Label x={840} y={702} size={40} anchor="middle">
        1000 м
      </Label>
    </>
  );
}

function Pressure({profiles, seg, time}: {profiles: Profile[]; seg: TimingSegment; time: number}) {
  const idx =
    seg.index === 5 ? 3 : Math.min(3, Math.floor(4 * fraction((time - seg.t_start) / (seg.t_end - seg.t_start))));
  const pr = profiles[idx],
    zones = seg.index === 5;
  const x = (r: number) => 157 + ((r - 0.5) / 149.5) * 1470;
  const y = (p: number) => 542 - ((p - 12) / 13) * 286;
  const boundary = x(pr.R_psss_m);
  const late = zones || time >= cue(seg, 'ложке');
  return (
    <>
      <defs>
        <linearGradient id="pressure">
          {pr.curve.map((pt) => (
            <stop
              key={pt.r_m}
              offset={(pt.r_m - 0.5) / 149.5}
              stopColor={water}
              stopOpacity={Math.min(1, (25 - pt.p_MPa) / 5)}
            />
          ))}
        </linearGradient>
      </defs>
      <Label x={63} y={63} size={39} bold>
        {pr.t_sec} с от пуска
      </Label>
      <Label x={1710} y={63} size={34} color={muted} anchor="end">
        Профиль давления · κ = 1 м²/с · стр. 491
      </Label>
      <rect x={157} y={90} width={1470} height={93} fill="#746b57" />
      <rect x={157} y={90} width={1470} height={93} fill="url(#pressure)" />
      <rect x={157} y={90} width={1470} height={93} fill="url(#hatch)" />
      <Label x={173} y={151} size={35} bold>
        {zones ? 'Падение заметно' : '← К скважине'}
      </Label>
      {zones && (
        <>
          <Label x={boundary + 12} y={128} size={29}>
            Почти исходное
          </Label>
          <Label x={boundary + 12} y={163} size={29}>
            давление
          </Label>
          <Line x1={boundary} x2={boundary} y1={85} y2={548} color={amber} dash="12 10" weight={4} />
        </>
      )}
      {[15, 20, 25].map((p) => (
        <g key={p}>
          <Line x1={157} x2={1627} y1={y(p)} y2={y(p)} dash="5 8" />
          <Label x={137} y={y(p) + 10} anchor="end" size={32}>
            {p}
          </Label>
        </g>
      ))}
      <text transform="translate(54 391) rotate(-90)" textAnchor="middle" fill={ink} fontSize={36}>
        Давление, МПа
      </text>
      <Label x={1620} y={234} size={28} color={muted} anchor="end">
        Исходное давление
      </Label>
      <polyline
        points={pr.curve.map((p) => `${x(p.r_m)},${y(p.p_MPa)}`).join(' ')}
        fill="none"
        stroke={mint}
        strokeWidth={7}
      />
      <Line x1={157} x2={1627} y1={544} y2={544} />
      {[0.5, 50, 100, 150].map((r) => (
        <Label key={r} x={x(r)} y={585} size={32} anchor="middle">
          {String(r).replace('.', ',')}
        </Label>
      ))}
      <Label x={894} y={633} size={38} anchor="middle">
        Расстояние от скважины, м
      </Label>
      {late && (
        <Label x={65} y={688} size={34} color={mint}>
          {zones ? 'Пунктир — условная граница двух зон · стр. 494' : 'Обычно 0,1–5 м²/с · пьезопроводность · стр. 481'}
        </Label>
      )}
    </>
  );
}

function Radius({task, answer, progress}: {task: RadiusTask; answer: boolean; progress: number}) {
  const x = (h: number) => 188 + h * 279,
    y = (r: number) => 500 - r * 1.67;
  const end = answer ? 1 + 3 * ease(progress) : 1;
  const pts = task.curve.filter((pt) => pt.hours <= end);
  return (
    <>
      <Label x={67} y={74} size={37} color={muted}>
        Условие задачи · κ = {task.kappa_m2_s.toFixed(3).replace('.', ',')} м²/с
      </Label>
      {[0, 100, 200].map((r) => (
        <g key={r}>
          <Line x1={188} x2={1583} y1={y(r)} y2={y(r)} dash="6 8" />
          <Label x={163} y={y(r) + 11} size={35} anchor="end">
            {r}
          </Label>
        </g>
      ))}
      <text transform="translate(66 319) rotate(-90)" textAnchor="middle" fill={ink} fontSize="38">
        Радиус зоны, м
      </text>
      {[0, 1, 2, 3, 4, 5].map((h) => (
        <g key={h}>
          <Line x1={x(h)} x2={x(h)} y1={494} y2={511} />
          <Label x={x(h)} y={550} size={34} anchor="middle">
            {h}
          </Label>
        </g>
      ))}
      <polyline
        points={pts.map((pt) => `${x(pt.hours)},${y(pt.radius_m)}`).join(' ')}
        fill="none"
        stroke={mint}
        strokeWidth={8}
      />
      <circle cx={x(1)} cy={y(100)} r={12} fill={amber} />
      <Label x={x(1) + 18} y={y(100) + 54} size={39} color={amber} bold>
        1 ч · 100 м
      </Label>
      {answer ? (
        <>
          {progress >= 1 && (
            <>
              <circle cx={x(4)} cy={y(200)} r={13} fill={mint} />
              <Label x={x(4) + 16} y={y(200) - 20} size={39} bold>
                4 ч · 200 м
              </Label>
            </>
          )}
          <path d={`M${x(1)} 570v18h${3 * 279}v-18`} fill="none" stroke={amber} strokeWidth="4" />
          <Label x={x(2.5)} y={635} anchor="middle" size={36} bold color={amber}>
            Ещё +3 ч
          </Label>
        </>
      ) : (
        <>
          <Label x={206} y={y(200) - 19} size={44} bold color={amber}>
            ?
          </Label>
          <Label x={894} y={616} size={41} anchor="middle">
            Ещё 1 ч или ещё 3 ч?
          </Label>
        </>
      )}
      <Label x={890} y={693} size={38} anchor="middle">
        Время от пуска, ч
      </Label>
    </>
  );
}

function BoreComparison() {
  return (
    <>
      <Label x={67} y={83} size={45} bold>
        Давление у забоя · два расчёта
      </Label>
      {[false, true].map((wide, i) => (
        <g key={i} transform={`translate(${490 + i * 790} 308)`}>
          {[150, 115, 82].map((r) => (
            <circle key={r} r={r} fill="none" stroke={grid} strokeWidth={3} />
          ))}
          <circle r={wide ? 51 : 9} fill={wide ? panel : amber} stroke={amber} strokeWidth={6} />
          {wide && (
            <>
              <Line x1={0} y1={0} x2={51} y2={0} color={ink} />
              <Label x={68} y={15} size={31}>
                rс
              </Label>
            </>
          )}
          <Label x={0} y={218} anchor="middle" size={43}>
            {wide ? 'Ствол конечной ширины' : 'Скважина-точка'}
          </Label>
        </g>
      ))}
      <Label x={889} y={610} anchor="middle" size={51} color={mint} bold>
        Расхождение ≤ 0,6%
      </Label>
      <Label x={889} y={675} anchor="middle" size={35} color={muted}>
        1 с от пуска · rс = 0,1 м · κ = 1 м²/с · стр. 492
      </Label>
    </>
  );
}

function PoreGraph({local}: {local: number}) {
  const nodes = Array.from({length: 24}, (_, i) => ({
    x: 193 + (i % 8) * 197,
    y: 225 + Math.floor(i / 8) * 126 + (i % 2) * 25,
  }));
  return (
    <>
      <Label x={65} y={71} size={40} bold>
        Граф пор
      </Label>
      <Label x={1708} y={71} size={34} anchor="end" color={muted}>
        Толщина связи показывает проводимость
      </Label>
      <Line x1={1085} y1={125} x2={1085} y2={579} color={amber} dash="14 12" weight={4} />
      {nodes.map((n, i) => (
        <g key={i}>
          {[...(i % 8 < 7 ? [i + 1] : []), ...(i < 16 ? [i + 8] : [])].map((j) => (
            <Line
              key={j}
              x1={n.x}
              y1={n.y}
              x2={nodes[j].x}
              y2={nodes[j].y}
              color={i % 8 >= 4 ? muted : mint}
              weight={i % 8 >= 4 ? 3 : 12}
            />
          ))}
        </g>
      ))}
      {nodes.map((n, i) => (
        <circle
          key={i}
          cx={n.x}
          cy={n.y}
          r={21}
          fill={n.x < 200 + local * 180 ? (i % 8 < 5 ? mint : '#9bbdbe') : panel}
          stroke={ink}
          strokeWidth={4}
        />
      ))}
      <Label x={555} y={644} size={41} anchor="middle">
        Связанные поры
      </Label>
      <Label x={1390} y={644} size={41} color={amber} anchor="middle">
        Другая порода · связи слабее
      </Label>
    </>
  );
}

function Recovery({local}: {local: number}) {
  const path = 'M620 464 C680 359 767 286 934 251 S1321 207 1610 203';
  return (
    <>
      <Label x={65} y={75} size={42} bold>
        Отбор остановлен
      </Label>
      <Label x={1695} y={75} size={37} anchor="end" color={muted}>
        Пример формы записи
      </Label>
      <rect x={60} y={240} width={357} height={190} fill={grain} />
      <rect x={60} y={212} width={357} height={32} fill="#819587" />
      <path d="M180 142v239h34V142 M164 143h67" fill={panel} stroke={ink} strokeWidth={6} />
      <rect x={182} y={343} width={30} height={36} rx={6} fill={amber} />
      <Label x={66} y={501} size={37}>
        Датчик у забоя
      </Label>
      <text transform="translate(530 325) rotate(-90)" textAnchor="middle" fontSize={36} fill={ink}>
        Давление у забоя
      </text>
      <path d="M610 142v353h1030" stroke={grid} strokeWidth={5} fill="none" />
      <path
        d={path}
        stroke={blue}
        strokeWidth={8}
        fill="none"
        pathLength={1}
        strokeDasharray="1 1"
        strokeDashoffset={1 - fraction(local / 2)}
      />
      <Label x={1124} y={548} size={37} anchor="middle">
        Время после остановки
      </Label>
      <Label x={65} y={655} size={35} color={muted}>
        Гипотезы
      </Label>
      <Label x={328} y={655} size={39}>
        Без преграды
      </Label>
      <Label x={780} y={655} size={39} color={amber}>
        С непроницаемой границей (разлом)
      </Label>
    </>
  );
}

export const FieldDiffusion2D: React.FC<{props: VideoProps}> = ({props}) => {
  const t = useCurrentFrame() / useVideoConfig().fps;
  const {seg} = getCurrentState(t, props.timings.segments, props.passport.frames);
  const i = seg.index,
    local = t - seg.t_start;
  const comparison = i === 7 && t >= cue(seg, 'А если учесть ширину');
  const answer = i === 7 && t >= (seg.words[0]?.start ?? seg.t_start);
  const factor = 20 - 19 * ease(local / 1.4);
  const scale = i === 3 ? (factor === 1 ? '1:1 по обеим осям' : `Вертикаль ×${factor.toFixed(1)}`) : undefined;
  let body: React.ReactNode;
  if (i <= 2) body = <Mechanism seg={seg} t={t} />;
  else if (i === 3) body = <ScaleSection factor={factor} />;
  else if (i <= 5) body = <Pressure profiles={props.scene.state_sample.profiles} seg={seg} time={t} />;
  else if (comparison) body = <BoreComparison />;
  else if (i <= 7)
    body = <Radius task={props.scene.state_sample.radius_task} answer={answer} progress={local / 0.65} />;
  else if (i === 8) body = <PoreGraph local={local} />;
  else body = <Recovery local={local} />;
  let heading: string | undefined;
  if (i === 3 && factor > 1) heading = 'Снимаем растяжение';
  if (i === 2 && t < cue(seg, 'Тенгизе')) heading = 'От одной поры к пласту';
  if (i === 7)
    heading = comparison ? 'Влияет ли ширина скважины?' : answer ? 'Четыре часа от пуска' : 'Ещё час или три?';
  return (
    <FrameChrome
      props={props}
      heading={heading}
      scaleText={scale}
      isTrueScale={i === 3 && factor === 1}
      provenanceOverride={i >= 4 && i <= 7 && !comparison ? 'симуляция' : 'схема'}
    >
      <Diagram w={1780} h={710}>
        {body}
      </Diagram>
    </FrameChrome>
  );
};
