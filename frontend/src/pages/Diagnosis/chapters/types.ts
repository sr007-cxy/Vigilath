// Unified chapter props -- Report.tsx's table-driven layout treats every chapter the same
import type { ReactElement } from 'react';
import type {
  FullDiagnosisReport, DiagnosisScores, DiagnosisNarrative, DiagnosisSubResult,
} from '../../../services/fullDiagnosisApi';

export interface ChapterCtx {
  report: FullDiagnosisReport;
  scores: DiagnosisScores | null;
  /** Already defaulted via ?? {}, so chapters can read keys directly */
  n: DiagnosisNarrative;
  /** Already defaulted via ?? {}; always read data through reportKit's subData(), never read .data directly */
  sub: Record<string, DiagnosisSubResult>;
}

export type ChapterComp = (p: ChapterCtx) => ReactElement;
