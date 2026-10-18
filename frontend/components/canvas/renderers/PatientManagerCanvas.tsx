"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { ArrowRight, BriefcaseBusiness, Check, ChevronDown, Ellipsis, FileText, FolderOpen, Library, LifeBuoy, Loader2, Pencil, Pill, Plus, Search, Trash2, User } from "lucide-react";
import { cn } from "@/lib/utils";
import { ExpertCanvasTab, PatientManagerState } from "@/lib/types/vania";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { DynamicForm } from "@/components/tool-ui/form/dynamic-form";
import { ProfileTab } from "./tabs/ProfileTab";
import { RoadmapTab } from "./tabs/RoadmapTab";
import { RescueNetTab } from "./tabs/RescueNetTab";
import { MedicationsTab } from "./tabs/MedicationsTab";
import { AppendixTab } from "./tabs/AppendixTab";
import { FormsTab } from "./tabs/FormsTab";
import { API_BASE_URL, getAuthHeaders } from "@/lib/api";
import { useCanvasStore } from "@/lib/canvas/store";
import { BaseInfoPanel } from "./shared/BaseInfoPanel";
import { CaseFilesTab } from "./shared/CaseFilesTab";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useVaniaStore } from "@/lib/vania/store";

interface Props {
  canvasId?: string;
  data: PatientManagerState;
  onEdit: (delta: Partial<PatientManagerState>) => void;
  isLocked: boolean;
}

type CaseDialogMode = "create" | "edit" | "delete" | null;

interface VisitorOption {
  id: number;
  full_name?: string;
  phone_number: string;
}

const EMPTY_CASE = (
  id: string,
  title: string,
  options?: {
    doctorId?: number | null;
    doctorName?: string;
    visibleTabs?: ExpertCanvasTab[];
    caseOverviewSections?: string[];
    allowedFormKeys?: string[];
    testMode?: PatientManagerState["test_mode"];
    featurePolicy?: PatientManagerState["feature_policy"];
  }
) => ({
  id,
  title,
  clinical_summary: "",
  summary_voice_notes: [],
  forms_tests_analysis: "",
  roadmap_data: {
    current_phase: "PHASE_1_ANALYSIS" as const,
    treatment_approaches: [],
    sessions: [],
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  },
  active_goals: [],
  appendix_data: { resources: [] },
  medications: [],
  tasks: [],
  forms: [],
  tests: [],
  files: [],
  sessions: [],
  doctor_id: options?.doctorId || null,
  doctor_name: options?.doctorName,
  visible_tabs: (options?.visibleTabs || ["CASE_OVERVIEW"]) as ExpertCanvasTab[],
  case_overview_sections: options?.caseOverviewSections || [
    "clinical_summary",
    "forms_tests_analysis",
    "forms",
    "tests",
  ],
  allowed_form_keys: options?.allowedFormKeys || [],
  test_mode: options?.testMode || "disabled",
  feature_policy: options?.featurePolicy,
});

const replaceCaseScopedEntries = <T extends { case_id?: string | null }>(
  existingEntries: T[] = [],
  caseEntries: T[] = [],
  caseId?: string | null
) => {
  if (!caseId) return existingEntries;
  const untouchedEntries = (existingEntries || []).filter((entry) => entry?.case_id !== caseId);
  return [...caseEntries, ...untouchedEntries];
};

export default function PatientManagerCanvas({ canvasId, data, onEdit, isLocked }: Props) {
  const params = useParams();
  const router = useRouter();
  const setInstances = useCanvasStore((s) => s.setInstances);
  const updateCanvasInstance = useCanvasStore((s) => s.updateCanvas);
  const syncCanvasInstance = useCanvasStore((s) => s.syncCanvasInstance);
  const { activePatientId, setActivePatient } = useVaniaStore();

  const threadId = params.threadId as string | undefined;
  const agentId = params.agentId as string | undefined;

  const [activeView, setActiveView] = useState<"BASE" | "CASES">(data.active_view || "BASE");
  const [activeTab, setActiveTab] = useState<string>(data.active_tab || "CASE_OVERVIEW");
  const [dialogMode, setDialogMode] = useState<CaseDialogMode>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const [isHydratingCase, setIsHydratingCase] = useState(false);
  const [baseInfoOpen, setBaseInfoOpen] = useState(false);
  const [visitorQuery, setVisitorQuery] = useState("");
  const [availableVisitors, setAvailableVisitors] = useState<VisitorOption[]>([]);
  const [hasLoadedVisitors, setHasLoadedVisitors] = useState(false);
  const [isLoadingVisitors, setIsLoadingVisitors] = useState(false);
  const shouldShowVisitorPicker = !data?.is_active || !data?.patient_profile;

  useEffect(() => {
    if (data.active_view && data.active_view !== activeView) setActiveView(data.active_view);
  }, [data.active_view, activeView]);

  useEffect(() => {
    if (data.active_tab && data.active_tab !== activeTab) setActiveTab(data.active_tab);
  }, [data.active_tab, activeTab]);

  useEffect(() => {
    if (!shouldShowVisitorPicker || hasLoadedVisitors) return;

    const headers = getAuthHeaders();
    if (!headers.Authorization) {
      setHasLoadedVisitors(true);
      return;
    }

    let cancelled = false;

    const loadVisitors = async () => {
      setIsLoadingVisitors(true);
      try {
        const res = await fetch(`${API_BASE_URL}/api/vania/my-visitors/`, { headers });
        if (!res.ok) throw new Error("Failed to fetch visitors");

        const items = await res.json();
        if (cancelled) return;

        const safeItems = Array.isArray(items) ? items : [];
        const validVisitors: VisitorOption[] = safeItems
          .filter((item: any) => item?.patient_id !== null && item?.status === "ACTIVE")
          .map((item: any) => ({
            id: item.patient_id,
            full_name: item.name,
            phone_number: item.phone,
          }));

        const uniqueVisitors = Array.from(new Map(validVisitors.map((item) => [item.id, item])).values());
        setAvailableVisitors(uniqueVisitors);
      } catch (error) {
        if (!cancelled) {
          console.error("Failed to fetch visitor list for patient manager canvas:", error);
          setAvailableVisitors([]);
        }
      } finally {
        if (!cancelled) {
          setHasLoadedVisitors(true);
          setIsLoadingVisitors(false);
        }
      }
    };

    loadVisitors();

    return () => {
      cancelled = true;
    };
  }, [shouldShowVisitorPicker, hasLoadedVisitors]);

  const selectedCase = data.selected_case;
  const selectedCaseId = data.selected_case_id;
  const isSelectedCaseReadOnly = !!selectedCase?.is_read_only || selectedCase?.can_edit === false;
  const visibleTabs = (selectedCase?.visible_tabs || data.visible_tabs || ["CASE_OVERVIEW"]) as ExpertCanvasTab[];
  const featurePolicy = selectedCase?.feature_policy || data.feature_policy;
  const caseOverviewSections = selectedCase?.case_overview_sections || data.case_overview_sections || [];
  const baseProfileForm = data.base_profile?.form || {};
  const baseProfileDefinition = (data.available_forms || []).find((form) => form.key === "BASE_PROFILE_V1");
  const selectedCaseMeta = useMemo(
    () => data.cases?.find((item) => item.id === selectedCaseId) || data.cases?.[0],
    [data.cases, selectedCaseId]
  );
  const filteredVisitors = useMemo(() => {
    const normalizedQuery = visitorQuery.trim().toLocaleLowerCase("fa-IR");
    if (!normalizedQuery) return availableVisitors;

    return availableVisitors.filter((visitor) => {
      const haystack = `${visitor.full_name || ""} ${visitor.phone_number}`.toLocaleLowerCase("fa-IR");
      return haystack.includes(normalizedQuery);
    });
  }, [availableVisitors, visitorQuery]);
  const selectedDoctorId = selectedCaseMeta?.doctor_id || selectedCase?.doctor_id || null;

  useEffect(() => {
    if (activeView !== "CASES" || !visibleTabs.length) return;
    if (!visibleTabs.includes(activeTab as ExpertCanvasTab)) {
      const nextTab = visibleTabs[0];
      setActiveTab(nextTab);
      onEdit({ active_tab: nextTab } as any);
    }
  }, [activeTab, activeView, onEdit, visibleTabs]);

  const handleVisitorSelect = (visitor: VisitorOption) => {
    setActivePatient(visitor.id, visitor.full_name || visitor.phone_number);
    const newThreadId = `local-${crypto.randomUUID()}`;
    router.push(`/chat/${agentId}/${newThreadId}?visitorId=${visitor.id}`);
  };

  if (shouldShowVisitorPicker) {
    return (
      <div className="flex h-full flex-col bg-background" dir="rtl">
        <div className="border-b border-border/40 bg-background/80 px-4 py-4 sm:px-6">
          <div className="space-y-1">
            <h1 className="text-xl font-bold">مدیریت مراجع</h1>
            <p className="text-xs text-muted-foreground">یک مراجع را انتخاب کنید.</p>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto bg-muted/5 p-3 sm:p-6">
          <div className="mx-auto flex h-full max-w-5xl flex-col overflow-hidden rounded-[28px] border border-border/50 bg-card/95 shadow-[0_24px_80px_-48px_rgba(0,0,0,0.65)]">
            <div className="border-b border-border/50 bg-gradient-to-b from-muted/20 to-transparent px-4 py-5 sm:px-6">
              <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
                <div className="space-y-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="text-lg font-semibold text-foreground">مراجعان</h3>
                    <div className="rounded-full border border-border/60 bg-background/70 px-2.5 py-1 text-[11px] text-muted-foreground">
                      {(filteredVisitors.length || 0).toLocaleString("fa-IR")} مورد
                    </div>
                  </div>
                  <p className="text-xs leading-6 text-muted-foreground">
                    نام یا شماره تماس را جستجو کنید و برای ورود مستقیم روی ردیف یا دکمه اقدام بزنید.
                  </p>
                </div>

                <div className="relative w-full sm:max-w-sm">
                  <Search className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    value={visitorQuery}
                    onChange={(e) => setVisitorQuery(e.target.value)}
                    placeholder="جستجوی نام یا شماره تماس..."
                    className="h-11 rounded-2xl border-border/60 bg-background/80 pr-10 text-sm shadow-sm"
                  />
                </div>
              </div>
            </div>

            <div className="min-h-0 flex-1 px-3 py-3 sm:px-5">
              <div className="overflow-hidden rounded-2xl border border-border/50 bg-background/40">
              <Table>
                <TableHeader className="bg-muted/25 backdrop-blur-sm">
                  <TableRow className="hover:bg-transparent">
                    <TableHead className="h-12 pr-5 text-right text-xs font-semibold text-muted-foreground">نام مراجع</TableHead>
                    <TableHead className="text-right text-xs font-semibold text-muted-foreground">شماره تماس</TableHead>
                    <TableHead className="w-[160px] pl-5 text-left text-xs font-semibold text-muted-foreground">اقدام</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {isLoadingVisitors ? (
                    <TableRow>
                      <TableCell colSpan={3} className="h-40">
                        <div className="flex items-center justify-center gap-2 text-sm text-muted-foreground">
                          <Loader2 className="h-4 w-4 animate-spin" />
                          در حال بارگذاری...
                        </div>
                      </TableCell>
                    </TableRow>
                  ) : filteredVisitors.length ? (
                    filteredVisitors.map((visitor) => {
                      const isSelected = activePatientId === visitor.id;

                      return (
                        <TableRow
                          key={visitor.id}
                          data-state={isSelected ? "selected" : undefined}
                          className="group cursor-pointer border-border/40 hover:bg-muted/20 data-[state=selected]:bg-primary/5"
                          onClick={() => handleVisitorSelect(visitor)}
                        >
                          <TableCell className="pr-5 py-4">
                            <div className="flex items-center gap-3">
                              <div className={cn("flex h-11 w-11 items-center justify-center rounded-2xl border text-sm font-semibold transition", isSelected ? "border-primary/30 bg-primary/10 text-primary" : "border-border/60 bg-muted/30 text-muted-foreground group-hover:border-primary/20 group-hover:text-foreground")}>
                                {(visitor.full_name || "مراجع").slice(0, 1)}
                              </div>
                              <div className="min-w-0">
                                <div className="truncate font-medium">{visitor.full_name || "مراجع بدون نام"}</div>
                                <div className="mt-1 text-[11px] text-muted-foreground">
                                  {isSelected ? "مراجع فعال" : `شناسه: ${Number(visitor.id).toLocaleString("fa-IR")}`}
                                </div>
                              </div>
                            </div>
                          </TableCell>
                          <TableCell className="py-4 font-mono text-xs text-muted-foreground">{visitor.phone_number}</TableCell>
                          <TableCell className="pl-5 text-left">
                            <Button type="button" size="sm" variant={isSelected ? "secondary" : "outline"} className={cn("h-9 rounded-xl px-4 text-xs", !isSelected && "border-border/60 bg-background/70 hover:bg-background")} onClick={(event) => {
                              event.stopPropagation();
                              handleVisitorSelect(visitor);
                            }}>
                              {isSelected ? "ادامه" : "ورود به پرونده"}
                            </Button>
                          </TableCell>
                        </TableRow>
                      );
                    })
                  ) : (
                    <TableRow>
                      <TableCell colSpan={3} className="h-40">
                        <div className="flex flex-col items-center justify-center gap-2 text-center text-sm text-muted-foreground">
                          <User className="h-6 w-6 opacity-50" />
                          <div>{visitorQuery.trim() ? "نتیجه‌ای پیدا نشد." : "مراجعی وجود ندارد."}</div>
                        </div>
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  const switchView = (view: "BASE" | "CASES") => {
    setActiveView(view);
    onEdit({ active_view: view } as any);
  };

  const switchTab = (tab: ExpertCanvasTab) => {
    setActiveTab(tab);
    onEdit({ active_tab: tab } as any);
  };

  const hydrateCaseFromServer = async (caseId: string) => {
    if (!threadId || !agentId || !selectedDoctorId) {
      return;
    }

    setIsHydratingCase(true);
    try {
      const headers = getAuthHeaders();
      if (!headers.Authorization) return;

      headers["X-Target-Resource-ID"] = String(data.patient_profile.id);
      headers["X-Target-Expert-ID"] = String(selectedDoctorId);
      headers["X-Target-Doctor-ID"] = String(selectedDoctorId);
      headers["X-Target-Case-ID"] = caseId;

      const query = new URLSearchParams({
        agent_id: agentId,
        visitor_id: String(data.patient_profile.id),
        patient_id: String(data.patient_profile.id),
        expert_id: String(selectedDoctorId),
        doctor_id: String(selectedDoctorId),
        case_id: caseId,
      });

      const res = await fetch(`${API_BASE_URL}/agent/canvas/state/${threadId}?${query.toString()}`, { headers });
      if (!res.ok) return;
      const body = await res.json();
      if (Array.isArray(body?.canvases)) {
        setInstances(body.canvases);
      }
    } finally {
      setIsHydratingCase(false);
    }
  };

  const handleCaseSelect = async (caseId: string) => {
    const nextCase = data.cases.find((item) => item.id === caseId);
    if (!nextCase) return;

    onEdit({
      active_view: "CASES",
      active_tab: ((nextCase as any).visible_tabs || visibleTabs || ["CASE_OVERVIEW"])[0],
      selected_case_id: caseId,
      selected_case: {
        ...(data.selected_case || {}),
        id: nextCase.id,
        title: nextCase.title,
        doctor_id: nextCase.doctor_id,
        doctor_name: nextCase.doctor_name,
      } as any,
    });

    await hydrateCaseFromServer(caseId);
  };

  const handleBackToBase = () => {
    switchView("BASE");
  };

  const handleBaseProfileSuccess = (formData: Record<string, any>) => {
    onEdit({
      base_profile: {
        ...data.base_profile,
        form: {
          ...baseProfileForm,
          ...formData,
          form_key: "BASE_PROFILE_V1",
          form_title: baseProfileDefinition?.title || "اطلاعات پایه",
        },
      },
      patient_profile: {
        ...data.patient_profile,
        name: formData.full_name || data.patient_profile.name,
      },
    } as any);
    setBaseInfoOpen(false);
  };

  const handleSelectedCaseEdit = (delta: Record<string, any>) => {
    if (!selectedCase) return;

    const nextSelectedCase = { ...selectedCase, ...delta };
    const nextDelta: Record<string, any> = { selected_case: nextSelectedCase };

    if (Array.isArray(delta.forms)) {
      nextDelta.base_profile = {
        ...data.base_profile,
        forms: replaceCaseScopedEntries(data.base_profile.forms || [], delta.forms, selectedCase.id),
      };
    }

    if (Array.isArray(delta.tests)) {
      nextDelta.base_profile = {
        ...(nextDelta.base_profile || data.base_profile),
        tests: replaceCaseScopedEntries(data.base_profile.tests || [], delta.tests, selectedCase.id),
      };
    }

    onEdit(nextDelta as any);
  };

  const openCreateDialog = () => {
    setDraftTitle(`پرونده ${((data.cases?.length || 0) + 1).toLocaleString("fa-IR")}`);
    setDialogMode("create");
  };

  const openEditDialog = () => {
    if (!selectedCaseMeta) return;
    setDraftTitle(selectedCaseMeta.title);
    setDialogMode("edit");
  };

  const openDeleteDialog = () => {
    if (!selectedCaseMeta) return;
    setDraftTitle(selectedCaseMeta.title);
    setDialogMode("delete");
  };

  const handleCreateCase = async () => {
    const title = draftTitle.trim();
    if (!title || !canvasId) return;

    const id = `draft-${Date.now()}`;
    const now = new Date().toISOString();
    const nextVisibleTabs = (selectedCase?.visible_tabs || data.visible_tabs || ["CASE_OVERVIEW"]) as ExpertCanvasTab[];
    const nextActiveTab = nextVisibleTabs[0] || "CASE_OVERVIEW";
    const nextCases = [
      {
        id,
        title,
        doctor_id: selectedDoctorId,
        doctor_name: selectedCaseMeta?.doctor_name || selectedCase?.doctor_name,
        created_at: now,
        updated_at: now,
      },
      ...(data.cases || []),
    ];

    const delta = {
      active_view: "CASES",
      active_tab: nextActiveTab,
      selected_case_id: id,
      cases: nextCases,
      selected_case: EMPTY_CASE(id, title, {
        doctorId: selectedDoctorId,
        doctorName: selectedCaseMeta?.doctor_name || selectedCase?.doctor_name,
        visibleTabs: nextVisibleTabs,
        caseOverviewSections: selectedCase?.case_overview_sections || data.case_overview_sections,
        allowedFormKeys: selectedCase?.allowed_form_keys || data.allowed_form_keys,
        testMode: selectedCase?.test_mode || data.test_mode,
        featurePolicy: selectedCase?.feature_policy || data.feature_policy,
      }) as any,
    } as Partial<PatientManagerState>;

    updateCanvasInstance(canvasId, delta, false, "AGENT");
    await syncCanvasInstance(canvasId, delta);

    setDialogMode(null);
    setDraftTitle("");
    await hydrateCaseFromServer(id);
  };

  const handleRenameCase = () => {
    const title = draftTitle.trim();
    if (!title || !selectedCaseMeta || !canvasId) return;

    const nextCases = (data.cases || []).map((item) =>
      item.id === selectedCaseMeta.id
        ? { ...item, title, updated_at: new Date().toISOString() }
        : item
    );

    const delta = {
      cases: nextCases,
      selected_case: selectedCase ? ({ ...selectedCase, title } as any) : selectedCase,
    } as Partial<PatientManagerState>;

    updateCanvasInstance(canvasId, delta, false, "AGENT");
    void syncCanvasInstance(canvasId, delta);

    setDialogMode(null);
    setDraftTitle("");
  };

  const handleDeleteCase = async () => {
    if (!selectedCaseMeta || !canvasId) return;

    const nextCases = (data.cases || []).filter((item) => item.id !== selectedCaseMeta.id);
    const fallbackCase = nextCases[0] || null;

    const delta = {
      cases: nextCases,
      selected_case_id: fallbackCase?.id || null,
      selected_case: fallbackCase
        ? ({
            ...(selectedCase && selectedCase.id === fallbackCase.id ? selectedCase : EMPTY_CASE(
              fallbackCase.id,
              fallbackCase.title,
              {
                doctorId: fallbackCase.doctor_id,
                doctorName: fallbackCase.doctor_name,
                visibleTabs: selectedCase?.visible_tabs || data.visible_tabs,
                caseOverviewSections: selectedCase?.case_overview_sections || data.case_overview_sections,
                allowedFormKeys: selectedCase?.allowed_form_keys || data.allowed_form_keys,
                testMode: selectedCase?.test_mode || data.test_mode,
                featurePolicy: selectedCase?.feature_policy || data.feature_policy,
              }
            )),
            id: fallbackCase.id,
            title: fallbackCase.title,
            doctor_id: fallbackCase.doctor_id,
            doctor_name: fallbackCase.doctor_name,
          } as any)
        : null,
      active_view: fallbackCase ? "CASES" : "BASE",
    } as Partial<PatientManagerState>;

    updateCanvasInstance(canvasId, delta, false, "AGENT");
    await syncCanvasInstance(canvasId, delta);

    setDialogMode(null);
    setDraftTitle("");

    if (fallbackCase?.id) {
      await hydrateCaseFromServer(fallbackCase.id);
    }
  };

  return (
    <div className="flex h-full flex-col bg-background" dir="rtl">
      <div className="shrink-0 border-b border-border/40 bg-background/80 px-4 py-4 sm:px-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0 space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="truncate text-xl font-bold">{data.patient_profile.name}</h1>
              {activeView === "CASES" && selectedCase ? (
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="h-8 max-w-full gap-1.5 rounded-full bg-muted/50 px-3 text-xs font-medium text-muted-foreground hover:bg-muted/70"
                    >
                      <span className="truncate">{selectedCase.title}</span>
                      <ChevronDown className="h-3.5 w-3.5 shrink-0" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="start" className="min-w-56 text-right">
                    {(data.cases || []).map((item) => {
                      const isActive = item.id === selectedCaseId;

                      return (
                        <DropdownMenuItem
                          key={item.id}
                          disabled={isActive || isHydratingCase}
                          onClick={() => handleCaseSelect(item.id)}
                          className="flex items-center justify-between gap-3"
                        >
                          <span className="truncate">{item.title}</span>
                          {isActive ? <Check className="h-4 w-4 text-primary" /> : null}
                        </DropdownMenuItem>
                      );
                    })}
                  </DropdownMenuContent>
                </DropdownMenu>
              ) : null}
            </div>
            <p className="text-xs text-muted-foreground">{data.patient_profile.phone}</p>
          </div>

          {activeView === "CASES" && selectedCase ? (
            <div className="flex items-center gap-2">
              {isSelectedCaseReadOnly ? (
                <div className="rounded-full bg-muted/60 px-3 py-1.5 text-xs text-muted-foreground">فقط مشاهده</div>
              ) : null}
              <Button type="button" variant="ghost" size="sm" className="h-8 gap-1.5 rounded-full px-3 text-xs" onClick={handleBackToBase}>
                <ArrowRight className="h-3.5 w-3.5" />
                بازگشت به اطلاعات پایه
              </Button>
              {!isSelectedCaseReadOnly ? <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="icon" className="h-8 w-8 rounded-full">
                    <Ellipsis className="h-3.5 w-3.5" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onClick={openEditDialog}>
                    <Pencil className="h-4 w-4" />
                    ویرایش نام
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={openDeleteDialog} variant="destructive">
                    <Trash2 className="h-4 w-4" />
                    حذف پرونده
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu> : null}
            </div>
          ) : (
            <div className="rounded-full bg-muted/50 px-3 py-1.5 text-xs text-muted-foreground">
              اطلاعات پایه
            </div>
          )}
        </div>

        {activeView === "CASES" && selectedCase && (
          <div className="mt-4 flex flex-wrap items-center gap-1.5 border-t border-border/40 pt-3">
            {[
              { id: "CASE_OVERVIEW", label: "پرونده", icon: FileText },
              { id: "ROADMAP", label: "سند پشتیبان", icon: BriefcaseBusiness },
              { id: "RESCUENET", label: "تور نجات", icon: LifeBuoy },
              { id: "MEDICATIONS", label: "شیوه و مصرف دارو", icon: Pill },
              { id: "APPENDIX", label: "پیوست اندیشه", icon: Library },
              { id: "FILES", label: "فایل‌ها", icon: FolderOpen },
            ].filter((tab) => visibleTabs.includes(tab.id as ExpertCanvasTab)).map((tab) => (
              <button
                key={tab.id}
                type="button"
                onClick={() => switchTab(tab.id as any)}
                className={cn(
                  "flex items-center gap-1.5 rounded-full px-2.5 py-1.5 text-[11px] transition",
                  activeTab === tab.id ? "bg-muted text-foreground" : "text-muted-foreground hover:text-foreground"
                )}
              >
                <tab.icon className="h-3.5 w-3.5" />
                {tab.label}
              </button>
            ))}
            {isHydratingCase && (
              <div className="mr-auto flex items-center gap-1 text-[11px] text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                در حال بارگذاری پرونده
              </div>
            )}
          </div>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto bg-muted/5 p-3 sm:p-6">
        {activeView === "BASE" && (
          <div className="space-y-8">
            <BaseInfoPanel
              profile={baseProfileForm}
              forms={data.base_profile.forms || []}
              tests={data.base_profile.tests || []}
              cases={data.cases || []}
              availableForms={data.available_forms || []}
              onOpenCase={handleCaseSelect}
              onEditProfile={() => setBaseInfoOpen(true)}
              canEditProfile={!!baseProfileDefinition}
              emptyCasesText="هنوز پرونده‌ای برای این مراجع ساخته نشده است."
              caseAction={
                <Button type="button" variant="outline" size="sm" className="gap-1.5" onClick={openCreateDialog}>
                  <Plus className="h-3.5 w-3.5" />
                  پرونده جدید
                </Button>
              }
            />
          </div>
        )}

        {activeView === "CASES" && selectedCase && activeTab === "CASE_OVERVIEW" && (
          <div className="space-y-10">
            <ProfileTab
              patientProfile={data.patient_profile}
              caseId={selectedCase.id}
              clinicalSummary={selectedCase.clinical_summary || ""}
              summaryVoiceNotes={selectedCase.summary_voice_notes || []}
              formsTestsAnalysis={selectedCase.forms_tests_analysis || ""}
              forms={caseOverviewSections.includes("forms") ? selectedCase.forms || [] : []}
              tests={caseOverviewSections.includes("tests") ? selectedCase.tests || [] : []}
              onEdit={handleSelectedCaseEdit}
              isLocked={isLocked || isSelectedCaseReadOnly}
              showClinicalSummary={caseOverviewSections.includes("clinical_summary")}
              showFormsTestsAnalysis={caseOverviewSections.includes("forms_tests_analysis")}
            />
            {(featurePolicy?.forms_enabled || featurePolicy?.form_history_visible || (selectedCase.test_mode || data.test_mode || "disabled") !== "disabled") ? (
            <FormsTab
              forms={caseOverviewSections.includes("forms") ? selectedCase.forms || [] : []}
              tests={caseOverviewSections.includes("tests") ? selectedCase.tests || [] : []}
              testsCatalog={data.tests_catalog || []}
              availableForms={(data.available_forms || []).filter(
                (form) =>
                  form.key !== "BASE_PROFILE_V1" &&
                  (selectedCase.allowed_form_keys || data.allowed_form_keys || []).includes(form.key)
              )}
              uiSignal={data.ui_signal}
              onEdit={handleSelectedCaseEdit}
              patientId={data.patient_profile.id}
              caseId={selectedCase.id}
              readOnly={isSelectedCaseReadOnly}
              formsEnabled={!!featurePolicy?.forms_enabled}
              formHistoryVisible={caseOverviewSections.includes("forms") && !!featurePolicy?.form_history_visible}
              testMode={selectedCase.test_mode || data.test_mode || "disabled"}
            />
            ) : null}
          </div>
        )}

        {activeView === "CASES" && selectedCase && activeTab === "ROADMAP" && featurePolicy?.roadmap_enabled && (
          <RoadmapTab
            roadmap={selectedCase.roadmap_data}
            activeGoals={selectedCase.active_goals || []}
            patientId={data.patient_profile.id}
            caseId={selectedCase.id}
            patientName={data.patient_profile.name}
            allSessionsHistory={selectedCase.sessions || []}
            onEdit={handleSelectedCaseEdit}
            readOnly={isSelectedCaseReadOnly}
          />
        )}

          {activeView === "CASES" && selectedCase && activeTab === "RESCUENET" && featurePolicy?.rescue_net_enabled && (
            <RescueNetTab
              tasks={selectedCase.tasks || []}
              patientId={data.patient_profile.id}
              caseId={selectedCase.id}
              patientName={data.patient_profile.name}
              caseTitle={selectedCase.title}
              onEdit={handleSelectedCaseEdit}
              readOnly={isSelectedCaseReadOnly}
            />
        )}

        {activeView === "CASES" && selectedCase && activeTab === "MEDICATIONS" && featurePolicy?.medications_enabled && (
          <MedicationsTab
            medications={selectedCase.medications || []}
            patientId={data.patient_profile.id}
            caseId={selectedCase.id}
            onEdit={handleSelectedCaseEdit}
            readOnly={isSelectedCaseReadOnly}
          />
        )}

          {activeView === "CASES" && selectedCase && activeTab === "APPENDIX" && featurePolicy?.appendix_enabled && (
            <AppendixTab
              library={selectedCase.appendix_data}
              patientId={data.patient_profile.id}
              caseId={selectedCase.id}
              patientName={data.patient_profile.name}
              caseTitle={selectedCase.title}
              onEdit={handleSelectedCaseEdit}
              readOnly={isSelectedCaseReadOnly}
            />
        )}

        {activeView === "CASES" && selectedCase && activeTab === "FILES" && featurePolicy?.files_enabled && (
          <CaseFilesTab
            files={selectedCase.files || []}
            selectedDoctorId={selectedDoctorId}
            selectedCaseId={selectedCase.id}
            patientId={data.patient_profile.id}
            onEdit={handleSelectedCaseEdit}
            readOnly={isSelectedCaseReadOnly}
          />
        )}
      </div>

      <Dialog open={dialogMode === "create" || dialogMode === "edit"} onOpenChange={(open) => !open && setDialogMode(null)}>
        <DialogContent dir="rtl" className="max-w-md">
          <DialogHeader>
            <DialogTitle>{dialogMode === "create" ? "ایجاد پرونده جدید" : "ویرایش نام پرونده"}</DialogTitle>
            <DialogDescription>
              {dialogMode === "create"
                ? "برای پرونده جدید یک نام مشخص و قابل تشخیص وارد کنید."
                : "نام جدید پرونده را ثبت کنید."}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-2">
            <Label htmlFor="case-title">نام پرونده</Label>
            <Input id="case-title" value={draftTitle} onChange={(e) => setDraftTitle(e.target.value)} placeholder="مثلا: پیگیری اضطراب خواب" />
          </div>

          <DialogFooter>
            <Button variant="ghost" onClick={() => setDialogMode(null)}>انصراف</Button>
            <Button onClick={dialogMode === "create" ? handleCreateCase : handleRenameCase} disabled={!draftTitle.trim()}>
              {dialogMode === "create" ? "ایجاد پرونده" : "ذخیره تغییرات"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={dialogMode === "delete"} onOpenChange={(open) => !open && setDialogMode(null)}>
        <DialogContent dir="rtl" className="max-w-md">
          <DialogHeader>
            <DialogTitle>حذف پرونده</DialogTitle>
            <DialogDescription>
              این عملیات قابل بازگشت نیست. پرونده «{selectedCaseMeta?.title || draftTitle}» از فهرست پرونده‌ها حذف می‌شود.
            </DialogDescription>
          </DialogHeader>

          <DialogFooter>
            <Button variant="ghost" onClick={() => setDialogMode(null)}>انصراف</Button>
            <Button variant="destructive" onClick={handleDeleteCase}>
              حذف پرونده
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={baseInfoOpen} onOpenChange={setBaseInfoOpen}>
        <DialogContent dir="rtl" className="w-[calc(100vw-2rem)] max-w-4xl max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{baseProfileDefinition?.title || "اطلاعات پایه"}</DialogTitle>
            <DialogDescription>
              این فرم به پروفایل مشترک مراجع تعلق دارد و با ویرایش آن، اطلاعات پایه برای خود او و متخصصان متصل به‌روزرسانی می‌شود.
            </DialogDescription>
          </DialogHeader>

          {baseProfileDefinition && (
            <DynamicForm
              formHandle={baseProfileDefinition.handler}
              schema={baseProfileDefinition.schema}
              prefill={{
                ...baseProfileForm,
                form_key: baseProfileDefinition.key,
                form_title: baseProfileDefinition.title,
              }}
              title={baseProfileDefinition.title}
              description={baseProfileDefinition.description}
              onSuccess={handleBaseProfileSuccess}
              patientId={data.patient_profile.id}
              sessionId={threadId}
            />
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
