import React from 'react';
import {interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import {VideoProps} from '../types';
import {FrameChrome, getCurrentState} from '../components/FrameChrome';

export const UniversalConceptScene: React.FC<{props: VideoProps}> = ({props}) => {
  const frame = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const t = frame / fps;
  const isPortrait = height > width;
  const {seg, inPause} = getCurrentState(t, props.timings.segments, props.passport.frames || []);
  const segIdx = seg?.index ?? 0;
  const state = props.scene.state_sample || {};
  const sg = props.styleguide;

  const seg3 = props.timings.segments[3];
  const inTrueScale = segIdx === 3;
  const collapseT = seg3 && segIdx === 3
    ? interpolate(t, [seg3.t_start, seg3.t_start + 0.8, seg3.t_end - 0.8, seg3.t_end], [0, 1, 1, 0], {
        extrapolateLeft: 'clamp',
        extrapolateRight: 'clamp',
      })
    : 0;
  const scaleFactor = Math.round(interpolate(collapseT, [0, 1], [20, 1]));
  const scaleText = inTrueScale ? 'Истинный масштаб 1:1' : `Схема (масштаб ×${scaleFactor})`;

  const svgMarkup: string | undefined = state.visual_svg;
  const steps: string[] = state.steps || [];
  const claims: {id: string; text: string; page_pdf?: number}[] = state.claims || [];

  return (
    <FrameChrome
      props={props}
      scaleText={scaleText}
      isTrueScale={inTrueScale}
      schoolBadge={`Шаблон: ${props.scene.template_id}`}
    >
      <div
        style={{
          width: '100%',
          height: '100%',
          backgroundColor: sg.panel || '#1A2129',
          borderRadius: 20,
          border: '2px solid #2E3B49',
          padding: isPortrait ? 32 : 36,
          display: 'flex',
          flexDirection: isPortrait ? 'column' : 'row',
          gap: 28,
          boxSizing: 'border-box',
          overflow: 'hidden',
        }}
      >
        {/* Левый / верхний блок — детерминированный SVG шага из kurs/visual.py */}
        <div
          style={{
            flex: isPortrait ? '1 1 56%' : '1 1 55%',
            backgroundColor: '#0F1419',
            borderRadius: 16,
            border: '1px solid #2C3946',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: 16,
            overflow: 'hidden',
          }}
        >
          {svgMarkup ? (
            <div
              style={{
                width: '100%',
                height: '100%',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                transform: inTrueScale ? 'scaleY(0.35)' : 'scaleY(1)',
                transition: 'transform 0.3s ease',
              }}
              dangerouslySetInnerHTML={{__html: svgMarkup}}
            />
          ) : (
            <div style={{fontSize: 28, color: '#9FB0C0', textAlign: 'center'}}>{props.passport.node_card.title}</div>
          )}
        </div>

        {/* Правый / нижний блок — опорные факты, шаги и вопрос */}
        <div
          style={{
            flex: isPortrait ? '1 1 44%' : '1 1 45%',
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'space-between',
            gap: 16,
          }}
        >
          <div style={{fontSize: isPortrait ? 28 : 26, fontWeight: 700, color: '#FFFFFF'}}>
            {state.title || props.passport.node_card.title}
          </div>

          <div style={{display: 'flex', flexDirection: 'column', gap: 12, flex: 1, overflow: 'hidden'}}>
            {(steps.length ? steps : claims.map((c) => `${c.page_pdf ? `[стр. ${c.page_pdf}] ` : ''}${c.text}`))
              .slice(0, 4)
              .map((line, i) => {
                const active = i === Math.min(3, segIdx);
                return (
                  <div
                    key={i}
                    style={{
                      backgroundColor: active ? '#22303D' : '#141B22',
                      border: `2px solid ${active ? '#2ECC71' : '#2A3642'}`,
                      borderRadius: 12,
                      padding: '12px 16px',
                      fontSize: isPortrait ? 23 : 21,
                      lineHeight: 1.32,
                      color: active ? '#FFFFFF' : '#B8C4D0',
                    }}
                  >
                    {line}
                  </div>
                );
              })}
          </div>

          {inPause || segIdx >= 6 ? (
            <div
              style={{
                backgroundColor: '#252112',
                border: '2px solid #F1C40F',
                borderRadius: 12,
                padding: '12px 16px',
                fontSize: isPortrait ? 22 : 20,
                color: '#F7DC6F',
              }}
            >
              {props.passport.transfer_question}
            </div>
          ) : null}
        </div>
      </div>
    </FrameChrome>
  );
};
