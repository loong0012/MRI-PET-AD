/**
 * 患者全景档案（Patient 360）API
 */
import { httpGet, httpPost } from '@/utils/request'

export interface PatientListItem {
  patientNo: string
  name: string
  gender: string
  age: number | null
  caseCount: number
  latestExamDate: string
  latestRiskLevel: string | null
  latestRiskScore: number | null
  department: string
}

export interface PatientCaseBrief {
  caseId: string
  examDate: string
  modality: string
  department: string
  status: string
  diagStatus: string
  riskLevel: string | null
  riskScore: number | null
  hasMRI: boolean
  hasPET: boolean
  createTime: string
  hasIntervention?: boolean
}

export interface RiskTrendPoint {
  date: string
  caseId: string
  score: number
  level: string
  version: number
  reviewStatus: string
}

export interface CognitionPoint {
  date: string
  caseId: string
  mmse: number | null
  moca: number | null
  cognitionChange: string
  visitType: string
}

export interface TimelineEvent {
  time: string
  kind: 'case' | 'analysis' | 'intervention' | 'visit'
  kindLabel: string
  caseId: string
  title: string
  desc: string
}

export interface PatientProfile {
  patient: { patientNo: string; name: string; gender: string; age: number | null }
  summary: {
    caseCount: number
    followUpCount: number
    interventionCount: number
    firstExamDate: string
    latestExamDate: string
    latestRiskLevel: string | null
    latestRiskScore: number | null
    mmseTrend: number | null
  }
  cases: PatientCaseBrief[]
  riskTrend: RiskTrendPoint[]
  cognition: CognitionPoint[]
  timeline: TimelineEvent[]
}

/** 患者清单（按 patientNo 聚合） */
export function apiPatientList(keyword = ''): Promise<{ list: PatientListItem[]; total: number }> {
  return httpGet('/patient/list', { keyword })
}

/** 患者全景聚合 */
export function apiPatientProfile(patientNo: string): Promise<PatientProfile | null> {
  return httpGet<PatientProfile | null>(`/patient/${encodeURIComponent(patientNo)}`)
}

/** 队列纵向轨迹对比（最多 8 名患者） */
export function apiCohortTrajectory(patientNos: string[]): Promise<{
  patients: {
    patientNo: string
    name: string
    gender: string
    age: number | null
    riskTrend: { date: string; score: number }[]
    mmseTrend: { date: string; mmse: number }[]
    caseCount: number
    visitCount: number
  }[]
}> {
  return httpPost('/patient/cohort-trajectory', { patientNos })
}
