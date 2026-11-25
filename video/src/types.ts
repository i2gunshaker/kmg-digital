export interface ScriptLine {
  t_start: number;
  t_end: number;
  text: string;
  claim_id: string | null;
  is_analogy?: boolean;
  on_screen?: string;
}

export interface TimingWord {
  word: string;
  start: number;
  end: number;
}

export interface TimingSegment {
  index: number;
  t_start: number;
  t_end: number;
  on_screen: string;
  claim_id: string | null;
  is_analogy: boolean;
  spoken_ref: string;
  pause_after_sec: number;
  pause_window?: [number, number] | null;
  words: TimingWord[];
}

export interface PassportFrame {
  t: number;
  class: 'A' | 'B' | 'C' | 'D';
  label: 'реальные данные' | 'симуляция' | 'схема';
}

export interface VideoProps {
  audioSrc: string;
  scene: {
    video_id: string;
    format: '9:16' | '16:9';
    resolution: [number, number];
    fps: number;
    duration_sec: number;
    template_id: string;
    parameters: Record<string, any>;
    state_sample: Record<string, any>;
  };
  passport: {
    video_id: string;
    node_id: string;
    node_card: {
      chapter: string;
      title: string;
      difficulty: {primary: string; secondary?: string | null};
      format: string;
      duration_sec: number;
    };
    script: ScriptLine[];
    frames: PassportFrame[];
    exaggerations: string[];
    prediction_question: string;
    transfer_question: string;
  };
  timings: {
    total_duration_sec: number;
    segments: TimingSegment[];
  };
  styleguide: Record<string, any>;
}
