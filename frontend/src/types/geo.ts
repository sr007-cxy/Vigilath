export interface CheckResult {
  category: string;
  status: string; // PASS, WARN, FAIL, INFO
  message: string;
  fix?: string;
}

export interface GeoTestRequest {
  url: string;
  include_fix: boolean;
}

export interface GeoTestResult {
  url: string;
  score: number;
  grade: string;
  checks: CheckResult[];
  summary: {
    pass_count: number;
    warn_count: number;
    fail_count: number;
    info_count: number;
    total_checks: number;
  };
}
