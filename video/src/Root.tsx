import React from 'react';
import {Composition} from 'remotion';
import {VideoProps} from './types';
import {WellboreLogSync} from './scenes/WellboreLogSync';
import {FieldDiffusion2D} from './scenes/FieldDiffusion2D';
import {UniversalConceptScene} from './scenes/UniversalConceptScene';

const SceneDispatcher: React.FC<VideoProps> = (props) => {
  const tpl = props.scene?.template_id || '';
  if (tpl === 'wellbore-log-sync' && props.scene?.state_sample?.profile_rows) {
    return <WellboreLogSync props={props} />;
  }
  if (tpl === 'field-diffusion-2d' && props.scene?.state_sample?.profiles) {
    return <FieldDiffusion2D props={props} />;
  }
  return <UniversalConceptScene props={props} />;
};

const defaultDummyProps: VideoProps = {
  audioSrc: '',
  scene: {
    video_id: 'demo',
    format: '9:16',
    resolution: [1080, 1920],
    fps: 30,
    duration_sec: 10,
    template_id: 'process-section',
    parameters: {},
    state_sample: {},
  },
  passport: {
    video_id: 'demo',
    node_id: 'demo.node',
    node_card: {chapter: 'demo', title: 'Демо-сцена', difficulty: {primary: 'М'}, format: 'шортс', duration_sec: 10},
    script: [{t_start: 0, t_end: 5, text: 'Демо', claim_id: null, on_screen: 'Демо'}],
    frames: [{t: 1, class: 'B', label: 'симуляция'}],
    exaggerations: [],
    prediction_question: '',
    transfer_question: '',
  },
  timings: {
    total_duration_sec: 10,
    segments: [{index: 0, t_start: 0, t_end: 5, on_screen: 'Демо', claim_id: null, is_analogy: false, spoken_ref: 'Демо', pause_after_sec: 0, words: []}],
  },
  styleguide: {background: '#0F1419', panel: '#1A2129', text: '#ECEFF4'},
};

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="KursVideo9x16"
        component={SceneDispatcher as unknown as React.FC<Record<string, unknown>>}
        durationInFrames={300}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={defaultDummyProps as unknown as Record<string, unknown>}
        calculateMetadata={({props}) => {
          const vp = props as unknown as VideoProps;
          const dur = Math.max(2, vp?.timings?.total_duration_sec || vp?.scene?.duration_sec || 10);
          return {
            durationInFrames: Math.ceil(dur * 30),
            fps: 30,
            width: 1080,
            height: 1920,
          };
        }}
      />
      <Composition
        id="KursVideo16x9"
        component={SceneDispatcher as unknown as React.FC<Record<string, unknown>>}
        durationInFrames={300}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={defaultDummyProps as unknown as Record<string, unknown>}
        calculateMetadata={({props}) => {
          const vp = props as unknown as VideoProps;
          const dur = Math.max(2, vp?.timings?.total_duration_sec || vp?.scene?.duration_sec || 10);
          return {
            durationInFrames: Math.ceil(dur * 30),
            fps: 30,
            width: 1920,
            height: 1080,
          };
        }}
      />
    </>
  );
};
