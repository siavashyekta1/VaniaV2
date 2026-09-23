"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertCircle, CheckCircle2, Loader2, Route, Search } from "lucide-react";
import { RoleGuard } from "@/components/role-guard";
import PatientJourneyCanvas from "@/components/canvas/renderers/PatientJourneyCanvas";
import { InteractiveTestResultView } from "@/components/canvas/renderers/shared/InteractiveTestResultView";
import { API_BASE_URL, fetcher, getAuthHeaders } from "@/lib/api";
import { useUser } from "@/hooks/use-user";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PatientJourneyState } from "@/lib/types/vania";


type JourneyState = PatientJourneyState;

type CanvasStateResponse = {
  canvases?: Array<{
    id: string;
    component_key: string;
    current_state: JourneyState;
  }>;
};

type EsanjAttempt = {
  id: string;
  invoice_id?: string | null;
  esanj_test_id: number;
  test_title: string;
  status: "IN_PROGRESS" | "SUBMITTED" | "COMPLETED" | "FAILED";
  result?: {
    json?: Record<string, any>;
    grading?: Record<string, any>;
  } | null;
  purchased_at?: string | null;
  started_at: string;
  completed_at?: string | null;
};

const VISITOR_AGENT_SLUG = "vania-visitor-companion";
const JOURNEY_NAVIGATION_KEYS = new Set(["active_view", "active_tab", "selected_case_id", "selected_doctor_id"]);

function deepMerge(target: any, source: any): any {
  if (typeof target !== "object" || target === null) return source;
  if (typeof source !== "object" || source === null) return source;

  const output = { ...target };
  for (const key of Object.keys(source)) {
    const sourceValue = source[key];
    const targetValue = output[key];
    if (Array.isArray(sourceValue)) output[key] = sourceValue;
    else if (typeof sourceValue === "object" && sourceValue !== null && targetValue) {
      output[key] = deepMerge(targetValue, sourceValue);
    } else output[key] = sourceValue;
  }
  return output;
}

function normalizeDashboardJourneyState(state: JourneyState, preserveSelection = false): JourneyState {
  if (preserveSelection) {
    return state;
  }

  const cases = Array.isArray(state.cases) ? state.cases : [];
  if (cases.length === 1 && state.selected_case?.id === cases[0].id) {
    return {
      ...state,
      active_view: "CASES",
      selected_case_id: cases[0].id,
      selected_doctor_id: cases[0].doctor_id,
    };
  }

  return {
    ...state,
    active_view: "BASE",
    selected_case_id: null,
    selected_doctor_id: null,
  };
}

function formatNumber(value: number | string | null | undefined) {
  if (value == null || value === "") return "";
  return new Intl.NumberFormat("fa-IR").format(Number(value));
}

function formatDate(value?: string | null) {
  if (!value) return "";
  return new Intl.DateTimeFormat("fa-IR-u-ca-persian", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export default function VisitorJourneyPage() {
  const { user } = useUser();
  const sessionId = useMemo(() => (user?.id ? `visitor-dashboard-${user.id}` : null), [user?.id]);
  const [doctorScopeId, setDoctorScopeId] = useState<string | null>(null);
  const [caseScopeId, setCaseScopeId] = useState<string | null>(null);

  const [canvasId, setCanvasId] = useState<string | null>(null);
  const [data, setData] = useState<JourneyState | null>(null);
  const [completedAttempts, setCompletedAttempts] = useState<EsanjAttempt[]>([]);
  const [selectedAttempt, setSelectedAttempt] = useState<EsanjAttempt | null>(null);
  const [testQuery, setTestQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [testsLoading, setTestsLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [testsError, setTestsError] = useState<string | null>(null);

  const filteredCompletedAttempts = useMemo(() => {
    const normalized = testQuery.trim().toLowerCase();
    if (!normalized) return completedAttempts;
    return completedAttempts.filter((attempt) => {
      return (
        attempt.test_title.toLowerCase().includes(normalized) ||
        String(attempt.esanj_test_id).includes(normalized) ||
        formatDate(attempt.purchased_at || attempt.started_at).includes(testQuery.trim()) ||
        formatDate(attempt.completed_at).includes(testQuery.trim())
      );
    });
  }, [completedAttempts, testQuery]);

  const loadCanvas = useCallback(async () => {
    if (!sessionId || !user?.id) return;
    setLoading(true);
    setError(null);

    try {
      const preserveSelection = Boolean(doctorScopeId || caseScopeId);
      const headers = getAuthHeaders();
      if (!headers.Authorization) return;
      headers["X-Target-Resource-ID"] = String(user.id);
      if (doctorScopeId) {
        headers["X-Target-Expert-ID"] = doctorScopeId;
        headers["X-Target-Doctor-ID"] = doctorScopeId;
      }
      if (caseScopeId) headers["X-Target-Case-ID"] = caseScopeId;

      const query = new URLSearchParams({ agent_id: VISITOR_AGENT_SLUG });
      query.set("visitor_id", String(user.id));
      query.set("patient_id", String(user.id));
      if (doctorScopeId) {
        query.set("doctor_id", doctorScopeId);
        query.set("expert_id", doctorScopeId);
      }
      if (caseScopeId) query.set("case_id", caseScopeId);

      const res = await fetch(`${API_BASE_URL}/agent/canvas/state/${sessionId}?${query.toString()}`, { headers });
      if (!res.ok) throw new Error("دریافت داشبورد با خطا مواجه شد.");

      const body: CanvasStateResponse = await res.json();
      const journey = body.canvases?.find((c) => c.component_key === "VANIA_PATIENT_JOURNEY");
      if (!journey) throw new Error("بوم مسیر مراجع یافت نشد.");

      const cases = Array.isArray(journey.current_state.cases) ? journey.current_state.cases : [];
      const onlyCase = !preserveSelection && cases.length === 1 ? cases[0] : null;
      if (
        onlyCase &&
        onlyCase.doctor_id != null &&
        journey.current_state.selected_case?.id !== onlyCase.id
      ) {
        const nextDoctorId = String(onlyCase.doctor_id);
        setDoctorScopeId(nextDoctorId);
        setCaseScopeId(String(onlyCase.id));
        localStorage.setItem(`vania:last_selected_doctor_by_patient:${user.id}`, nextDoctorId);
        return;
      }

      const normalizedState = normalizeDashboardJourneyState(journey.current_state, preserveSelection);
      setCanvasId(journey.id);
      setData(normalizedState);
    } catch (e: any) {
      setError(e?.message || "خطا در بارگذاری داشبورد.");
    } finally {
      setLoading(false);
    }
  }, [sessionId, doctorScopeId, caseScopeId, user?.id]);

  useEffect(() => {
    loadCanvas();
  }, [loadCanvas]);

  const loadCompletedTests = useCallback(async () => {
    setTestsLoading(true);
    setTestsError(null);
    try {
      const body = await fetcher<{ attempts: EsanjAttempt[] }>("/api/vania/esanj/attempts/");
      const completed = (body.attempts || []).filter((attempt) => attempt.status === "COMPLETED");
      setCompletedAttempts(completed);
      setSelectedAttempt((current) => {
        if (current) {
          const refreshed = completed.find((attempt) => attempt.id === current.id);
          if (refreshed) return refreshed;
        }
        return completed[0] || null;
      });
    } catch (e: any) {
      setTestsError(e?.message || "دریافت تست‌های انجام‌شده ناموفق بود.");
    } finally {
      setTestsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadCompletedTests();
  }, [loadCompletedTests]);

  const handleEdit = useCallback(async (delta: Partial<JourneyState>) => {
    if (!canvasId || !data) return;

    const selectedCaseId = delta.selected_case_id ? String(delta.selected_case_id) : null;
    const selectedCaseMeta = selectedCaseId ? (data.cases || []).find((item) => item.id === selectedCaseId) : null;
    const selectedDoctorId =
      delta.selected_doctor_id != null
        ? String(delta.selected_doctor_id)
        : selectedCaseMeta?.doctor_id != null
          ? String(selectedCaseMeta.doctor_id)
          : null;

      if (selectedCaseId && selectedDoctorId) {
        setDoctorScopeId(selectedDoctorId);
        setCaseScopeId(selectedCaseId);
        if (user?.id) {
          localStorage.setItem(`vania:last_selected_doctor_by_patient:${user.id}`, selectedDoctorId);
        }
      } else if (delta.active_view === "BASE") {
        setCaseScopeId(null);
        setDoctorScopeId(null);
      }

    const previous = data;
    setData((prev) => (prev ? deepMerge(prev, delta) : prev));
    if (selectedDoctorId && !selectedCaseId) {
      const nextDoctor = selectedDoctorId;
      setDoctorScopeId(nextDoctor);
      if (user?.id) {
        localStorage.setItem(`vania:last_selected_doctor_by_patient:${user.id}`, nextDoctor);
      }
    }
    if (selectedCaseId && !selectedDoctorId) {
      setCaseScopeId(selectedCaseId);
    }

    const persistentDelta = Object.fromEntries(
      Object.entries(delta).filter(([key]) => !JOURNEY_NAVIGATION_KEYS.has(key))
    );
    if (Object.keys(persistentDelta).length === 0) {
      return;
    }

    setSaving(true);

    try {
      const headers = getAuthHeaders();
      if (!headers.Authorization) throw new Error("نشست کاربر معتبر نیست.");
      const effectiveDoctorId =
        selectedDoctorId ||
        doctorScopeId ||
        (data.selected_case?.doctor_id != null ? String(data.selected_case.doctor_id) : null) ||
        (data.selected_doctor_id != null ? String(data.selected_doctor_id) : null);
      const effectiveCaseId =
        selectedCaseId ||
        caseScopeId ||
        (data.selected_case_id ? String(data.selected_case_id) : null);

      const res = await fetch(`${API_BASE_URL}/agent/canvas/instance/${canvasId}`, {
        method: "PATCH",
        headers: {
          ...headers,
          "Content-Type": "application/json",
          ...(user?.id ? { "X-Target-Resource-ID": String(user.id) } : {}),
          ...(effectiveDoctorId ? { "X-Target-Expert-ID": effectiveDoctorId } : {}),
          ...(effectiveDoctorId ? { "X-Target-Doctor-ID": effectiveDoctorId } : {}),
          ...(effectiveCaseId ? { "X-Target-Case-ID": effectiveCaseId } : {}),
        },
        body: JSON.stringify({ delta: persistentDelta }),
      });

      if (!res.ok) throw new Error("ذخیره تغییرات ناموفق بود.");
    } catch {
      setData(previous);
    } finally {
      setSaving(false);
    }
  }, [canvasId, data, doctorScopeId, caseScopeId, user?.id]);

  return (
    <RoleGuard allowedRoles={["visitor", "expert"]}>
      <div className="mx-auto flex h-full w-full max-w-6xl flex-col space-y-4 pb-6 pt-4" dir="rtl">
        <div className="flex items-center justify-between">
          <h1 className="flex items-center gap-2 text-xl font-bold sm:text-2xl">
            <Route className="h-5 w-5 text-primary" />
            داشبورد مسیر من
          </h1>
          {saving && (
            <div className="flex items-center gap-1 text-xs text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              در حال ذخیره...
            </div>
          )}
        </div>

        <Tabs defaultValue="journey" className="space-y-3">
          <TabsList className="h-auto w-full justify-start gap-1 rounded-xl bg-muted/60 p-1 sm:w-auto" dir="rtl">
            <TabsTrigger value="journey" className="rounded-lg px-4 py-2">
              مسیر من
            </TabsTrigger>
            <TabsTrigger value="completed-tests" className="rounded-lg px-4 py-2">
              تست‌های انجام شده
            </TabsTrigger>
          </TabsList>

          <TabsContent value="journey" className="mt-0">
            <div className="min-h-[70vh] overflow-hidden rounded-2xl border bg-background shadow-sm">
              {loading && (
                <div className="flex h-[70vh] items-center justify-center text-muted-foreground gap-2">
                  <Loader2 className="h-5 w-5 animate-spin" />
                  در حال بارگذاری داشبورد...
                </div>
              )}

              {!loading && error && (
                <div className="p-4">
                  <Alert variant="destructive">
                    <AlertCircle className="h-4 w-4" />
                    <AlertTitle>خطا</AlertTitle>
                    <AlertDescription>{error}</AlertDescription>
                  </Alert>
                </div>
              )}

              {!loading && !error && data && (
                <PatientJourneyCanvas data={data} onEdit={handleEdit} isLocked={false} />
              )}
            </div>
          </TabsContent>

          <TabsContent value="completed-tests" className="mt-0">
            <div className="min-h-[70vh] rounded-2xl border bg-background p-4 shadow-sm">
              <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                <div className="space-y-1">
                  <h2 className="flex items-center gap-2 text-base font-semibold">
                    <CheckCircle2 className="h-4 w-4 text-primary" />
                    تست‌های انجام شده
                  </h2>
                  <p className="text-xs leading-6 text-muted-foreground" dir="rtl">
                    نتیجه آزمون‌های تکمیل‌شده همراه با تاریخ خرید و انجام.
                  </p>
                </div>
                <div className="relative w-full lg:max-w-sm">
                  <Search className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    value={testQuery}
                    onChange={(event) => setTestQuery(event.target.value)}
                    placeholder="جستجوی نام آزمون، شناسه یا تاریخ"
                    className="pr-9"
                    dir="rtl"
                  />
                </div>
              </div>

              {testsLoading && (
                <div className="flex h-64 items-center justify-center gap-2 text-muted-foreground">
                  <Loader2 className="h-5 w-5 animate-spin" />
                  در حال دریافت تست‌ها...
                </div>
              )}

              {!testsLoading && testsError && (
                <Alert variant="destructive">
                  <AlertCircle className="h-4 w-4" />
                  <AlertTitle>خطا</AlertTitle>
                  <AlertDescription>{testsError}</AlertDescription>
                </Alert>
              )}

              {!testsLoading && !testsError && !completedAttempts.length && (
                <div className="rounded-lg border border-dashed bg-muted/10 px-4 py-12 text-center text-sm text-muted-foreground">
                  هنوز تست تکمیل‌شده‌ای برای نمایش وجود ندارد.
                </div>
              )}

              {!testsLoading && !testsError && completedAttempts.length > 0 && (
                <div className="grid gap-4 lg:grid-cols-[360px_minmax(0,1fr)]">
                  <div className="max-h-[62vh] space-y-2 overflow-y-auto pr-1">
                    {!filteredCompletedAttempts.length ? (
                      <div className="rounded-lg border bg-muted/10 px-4 py-8 text-center text-sm text-muted-foreground">
                        نتیجه‌ای مطابق جستجو پیدا نشد.
                      </div>
                    ) : (
                      filteredCompletedAttempts.map((attempt) => (
                        <button
                          key={attempt.id}
                          type="button"
                          onClick={() => setSelectedAttempt(attempt)}
                          className={`w-full rounded-lg border p-3 text-right transition-colors hover:border-primary/50 hover:bg-primary/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                            selectedAttempt?.id === attempt.id ? "border-primary bg-primary/5" : "bg-muted/10"
                          }`}
                        >
                          <div className="flex items-start justify-between gap-2" dir="rtl">
                            <span className="line-clamp-2 text-sm font-medium leading-6">{attempt.test_title}</span>
                            <Badge variant="outline" className="shrink-0">
                              #{formatNumber(attempt.esanj_test_id)}
                            </Badge>
                          </div>
                          <div className="mt-2 space-y-1 text-xs leading-6 text-muted-foreground">
                            <div>تاریخ خرید: {formatDate(attempt.purchased_at || attempt.started_at)}</div>
                            <div>تاریخ انجام: {formatDate(attempt.completed_at)}</div>
                          </div>
                        </button>
                      ))
                    )}
                  </div>

                  <div className="min-h-[320px] rounded-lg border bg-muted/10 p-4">
                    {selectedAttempt ? (
                      <div className="space-y-4">
                        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between" dir="rtl">
                          <div className="min-w-0">
                            <h3 className="text-base font-semibold leading-7">{selectedAttempt.test_title}</h3>
                            <p className="mt-1 text-xs leading-6 text-muted-foreground">
                              تاریخ خرید: {formatDate(selectedAttempt.purchased_at || selectedAttempt.started_at)}
                              {" · "}
                              تاریخ انجام: {formatDate(selectedAttempt.completed_at)}
                            </p>
                          </div>
                          <Button variant="outline" size="sm" onClick={loadCompletedTests}>
                            بروزرسانی
                          </Button>
                        </div>
                        <InteractiveTestResultView result={selectedAttempt.result} />
                      </div>
                    ) : (
                      <div className="flex h-full min-h-[280px] items-center justify-center text-sm text-muted-foreground">
                        یک نتیجه را انتخاب کنید.
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          </TabsContent>
        </Tabs>
      </div>
    </RoleGuard>
  );
}
