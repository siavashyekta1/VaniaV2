"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2, AlertCircle, Route } from "lucide-react";
import { RoleGuard } from "@/components/role-guard";
import PatientJourneyCanvas from "@/components/canvas/renderers/PatientJourneyCanvas";
import { API_BASE_URL, getAuthHeaders } from "@/lib/api";
import { useUser } from "@/hooks/use-user";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { PatientJourneyState } from "@/lib/types/vania";

type JourneyState = PatientJourneyState;

type CanvasStateResponse = {
  canvases?: Array<{
    id: string;
    component_key: string;
    current_state: JourneyState;
  }>;
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

export default function VisitorJourneyPage() {
  const { user } = useUser();
  const sessionId = useMemo(() => (user?.id ? `visitor-dashboard-${user.id}` : null), [user?.id]);
  const [doctorScopeId, setDoctorScopeId] = useState<string | null>(null);
  const [caseScopeId, setCaseScopeId] = useState<string | null>(null);

  const [canvasId, setCanvasId] = useState<string | null>(null);
  const [data, setData] = useState<JourneyState | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
      </div>
    </RoleGuard>
  );
}
