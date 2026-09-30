export type Role = "관리자" | "변호사" | "사무원";

export interface Me {
  user_id: string;
  name: string;
  role: Role;
  groups: string[];
  permissions: string[];
  must_change_password: boolean;
  csrf_token?: string;
}

export interface Matter {
  matter_id: string;
  case_no: string;
  case_type: string;
  court: string | null;
  plaintiff: string;
  defendant: string;
  our_side: "원고" | "피고";
  attorney_name: string | null;
  result: string;
  access_group: string;
  doc_count: number;
}

export type JobStatus =
  | "queued" | "parsing" | "needs_review" | "ready_to_index" | "indexing"
  | "done" | "failed" | "duplicate" | "rejected";

export interface Job {
  job_id: number;
  batch_id: number;
  orig_name: string;
  status: JobStatus;
  reason: string | null;
  attempts: number;
  matter_id: string | null;
  doc_kind: string | null;
  round: number | null;
  author_side: string | null;
  filed_date: string | null;
  issue_tags: string[];
  doc_id: string | null;
  created_by: string;
  access_group: string;
  created_at: string;
  updated_at: string;
  preview?: string | null;
  text_length?: number | null;
}

export interface JobList {
  counts: Partial<Record<JobStatus, number>>;
  items: Job[];
}

export interface DocItem {
  doc_id: string;
  matter_id: string;
  case_no: string;
  case_type: string;
  kind: string;
  round: number;
  author_side: string;
  filed_date: string | null;
  access_group: string;
  source_name: string | null;
  text_length: number;
  issue_tags: string[];
  full_text?: string;
  sections?: string[];
}

export interface UserRow {
  user_id: string;
  name: string;
  role: Role;
  active: boolean;
  groups: string[];
  created_at: string;
}

export interface GroupRow {
  group_id: string;
  description: string;
  members: number;
}

export interface UploadResult {
  batch_id: number;
  counts: Partial<Record<JobStatus, number>>;
  skipped_entries: number;
  jobs: { job_id: number; name: string; status: JobStatus; reason: string | null }[];
  jobs_truncated: boolean;
}

export const DOC_KINDS = ["준비서면", "소장", "답변서", "판결문", "계약서", "의견서", "기타"] as const;
export const ROLES: Role[] = ["관리자", "변호사", "사무원"];

export const STATUS_LABEL: Record<JobStatus, string> = {
  queued: "대기",
  parsing: "변환 중",
  needs_review: "검토 필요",
  ready_to_index: "색인 대기",
  indexing: "색인 중",
  done: "완료",
  failed: "실패",
  duplicate: "중복",
  rejected: "거부됨",
};

export const IN_PROGRESS: JobStatus[] = ["queued", "parsing", "ready_to_index", "indexing"];

/** 서면 종류는 DB에 "준 비 서 면"처럼 저장돼 있어 화면에서는 공백 없이 보여 준다. */
export const kindLabel = (k: string | null | undefined): string => (k ? k.replace(/\s+/g, "") : "—");
