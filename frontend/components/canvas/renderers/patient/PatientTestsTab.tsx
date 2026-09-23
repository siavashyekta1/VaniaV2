"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { CheckCircle2, CreditCard, FlaskConical, Loader2, Link2, PlayCircle, Upload } from "lucide-react";
import { toast } from "sonner";
import { ClinicalTestAttachment, ClinicalTestEntry } from "@/lib/types/vania";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { API_BASE_URL, getAuthHeaders } from "@/lib/api";
import { InteractiveTestResultView } from "../shared/InteractiveTestResultView";

type InteractiveAnswer = {
  row: number;
  title: string;
  value: string;
};

type InteractiveQuestion = {
  row: number;
  title: string;
  answers: InteractiveAnswer[];
};

type InteractiveAttempt = {
  id: string;
  clinical_test_id?: string;
  esanj_test_id: number;
  test_title: string;
  status: "IN_PROGRESS" | "SUBMITTED" | "COMPLETED" | "FAILED";
  age: number;
  sex: "male" | "female";
  answers: Record<string, string>;
  questionnaire?: {
    delivery_mode?: "html" | "json";
    questions?: InteractiveQuestion[];
  };
  progress?: {
    answered: number;
    total: number;
  };
  result?: {
    json?: Record<string, unknown>;
    grading?: Record<string, unknown>;
  } | null;
};

type TestPaymentRequest = {
  invoice_id: string;
  redirect_url: string;
  pricing?: {
    total_amount?: string;
    subtotal_amount?: string;
    tax_amount?: string;
    markup_percent?: string;
  };
  error?: string;
};

interface Props {
  tests: ClinicalTestEntry[];
  selectedDoctorId?: number | null;
  selectedCaseId?: string;
  onEdit: (delta: any) => void;
  title?: string;
  createLabel?: string;
  emptyText?: string;
}

const toJalali = (isoDateString?: string) => {
  if (!isoDateString) return "-";
  try {
    if (isoDateString.startsWith("13") || isoDateString.startsWith("14")) return isoDateString;
    return new Date(isoDateString).toLocaleDateString("fa-IR");
  } catch {
    return isoDateString;
  }
};

const getTestAttachments = (test: ClinicalTestEntry): ClinicalTestAttachment[] => {
  if (Array.isArray(test.attachments) && test.attachments.length > 0) {
    return test.attachments;
  }
  if (test.file_name) {
    return [{
      id: "legacy-file",
      file_name: test.file_name,
      file_path: test.file_path,
      file_uploaded_at: test.file_uploaded_at,
      content_type: test.file_name.toLowerCase().endsWith(".pdf") ? "application/pdf" : "application/octet-stream",
    }];
  }
  return [];
};

const interactiveStatusLabel = (status?: ClinicalTestEntry["interactive_status"] | InteractiveAttempt["status"]) => {
  if (status === "COMPLETED") return "تکمیل شده";
  if (status === "IN_PROGRESS" || status === "SUBMITTED") return "در حال انجام";
  if (status === "FAILED") return "نیازمند تلاش دوباره";
  return "در انتظار انجام";
};

const isInteractiveComplete = (test: ClinicalTestEntry) =>
  test.source === "interactive" && test.interactive_status === "COMPLETED";

const testHasResult = (test: ClinicalTestEntry) => {
  const attachments = getTestAttachments(test);
  const resultText = test.result_text || test.result_summary || "";
  return test.source === "interactive" ? isInteractiveComplete(test) : !!resultText.trim() || attachments.length > 0;
};

const formatNumber = (value?: string | number | null) => {
  if (value == null || value === "") return "";
  return new Intl.NumberFormat("fa-IR").format(Number(value));
};

export function PatientTestsTab({
  tests,
  selectedDoctorId,
  selectedCaseId,
  onEdit,
  title = "تست‌های من",
  createLabel = "آپلود نتیجه",
  emptyText = "هنوز تستی توسط متخصص ثبت نشده است.",
}: Props) {
  const router = useRouter();
  const [uploadOpen, setUploadOpen] = useState(false);
  const [activeTest, setActiveTest] = useState<ClinicalTestEntry | null>(null);
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [resultSummary, setResultSummary] = useState("");
  const [isUploading, setIsUploading] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [interactiveOpen, setInteractiveOpen] = useState(false);
  const [interactiveLoading, setInteractiveLoading] = useState(false);
  const [interactiveSubmitting, setInteractiveSubmitting] = useState(false);
  const [activeInteractiveTest, setActiveInteractiveTest] = useState<ClinicalTestEntry | null>(null);
  const [activeAttempt, setActiveAttempt] = useState<InteractiveAttempt | null>(null);
  const [age, setAge] = useState("");
  const [sex, setSex] = useState<"female" | "male">("female");
  const [questionIndex, setQuestionIndex] = useState(0);
  const [savingAnswerValue, setSavingAnswerValue] = useState<string | null>(null);
  const [paymentRequest, setPaymentRequest] = useState<TestPaymentRequest | null>(null);
  const onEditRef = useRef(onEdit);
  const testsRef = useRef(tests);

  useEffect(() => {
    onEditRef.current = onEdit;
  }, [onEdit]);

  useEffect(() => {
    testsRef.current = tests;
  }, [tests]);

  const refreshTests = useCallback(async (ignoreRef?: { current: boolean }) => {
      setIsLoading(true);
      try {
        const res = await fetch(`${API_BASE_URL}/api/vania/tests/`, {
          method: "GET",
          headers: {
            ...getAuthHeaders(),
            ...(selectedDoctorId ? { "X-Target-Expert-ID": String(selectedDoctorId) } : {}),
            ...(selectedDoctorId ? { "X-Target-Doctor-ID": String(selectedDoctorId) } : {}),
            ...(selectedCaseId ? { "X-Target-Case-ID": String(selectedCaseId) } : {}),
          },
        });
        if (!res.ok) return;
        const body = await res.json();
        if (!ignoreRef?.current && Array.isArray(body?.tests)) {
          const nextSerialized = JSON.stringify(body.tests);
          const currentSerialized = JSON.stringify(testsRef.current || []);
          if (nextSerialized !== currentSerialized) {
            onEditRef.current({ tests: body.tests });
          }
        }
      } catch {
      } finally {
        if (!ignoreRef?.current) setIsLoading(false);
      }
  }, [selectedDoctorId, selectedCaseId]);

  useEffect(() => {
    const ignoreRef = { current: false };

    refreshTests(ignoreRef);
    return () => {
      ignoreRef.current = true;
    };
  }, [refreshTests]);

  const activeQuestions = useMemo(
    () => activeAttempt?.questionnaire?.questions || [],
    [activeAttempt]
  );

  const activeQuestion = activeQuestions[questionIndex] || null;
  const answeredCount = activeAttempt ? Object.keys(activeAttempt.answers || {}).length : 0;
  const progressPercent = activeQuestions.length > 0 ? Math.round((answeredCount / activeQuestions.length) * 100) : 0;

  const sortedTests = useMemo(() => {
    return [...(tests || [])].sort((a, b) => {
      const aDone = testHasResult(a);
      const bDone = testHasResult(b);
      if (aDone !== bDone) return aDone ? 1 : -1;
      return new Date(b.created_at || 0).getTime() - new Date(a.created_at || 0).getTime();
    });
  }, [tests]);

  const testCounts = useMemo(() => {
    const total = tests?.length || 0;
    const done = (tests || []).filter(testHasResult).length;
    const interactive = (tests || []).filter((test) => test.source === "interactive").length;
    return { total, done, pending: total - done, interactive };
  }, [tests]);

  const openUpload = (test: ClinicalTestEntry) => {
    if (test.source === "interactive") {
      openInteractiveTest(test);
      return;
    }
    setActiveTest(test);
    setResultSummary(test.result_text || test.result_summary || "");
    setSelectedFiles([]);
    setUploadOpen(true);
  };

  const openInteractiveTest = async (test: ClinicalTestEntry) => {
    setActiveInteractiveTest(test);
    setActiveAttempt(null);
    setPaymentRequest(null);
    setQuestionIndex(0);
    setAge("");
    setSex("female");
    setInteractiveOpen(true);

    if (!test.interactive_attempt_id) return;

    setInteractiveLoading(true);
    try {
      const attempt = await fetchInteractiveAttempt(test.interactive_attempt_id);
      setActiveAttempt(attempt);
      const firstUnanswered = (attempt.questionnaire?.questions || []).findIndex((question) => !attempt.answers?.[String(question.row)]);
      setQuestionIndex(firstUnanswered >= 0 ? firstUnanswered : 0);
    } catch (e: any) {
      toast.error(e.message || "بارگذاری تست تعاملی ناموفق بود.");
    } finally {
      setInteractiveLoading(false);
    }
  };

  const fetchInteractiveAttempt = async (attemptId: string) => {
    const res = await fetch(`${API_BASE_URL}/api/vania/esanj/attempts/${attemptId}/`, {
      method: "GET",
      headers: { ...getAuthHeaders() },
    });
    if (!res.ok) throw new Error("بارگذاری تست تعاملی ناموفق بود.");
    return await res.json() as InteractiveAttempt;
  };

  const startInteractiveAttempt = async () => {
    if (!activeInteractiveTest?.interactive_test_id) return;
    const parsedAge = Number(age);
    if (!parsedAge || parsedAge < 1 || parsedAge > 150) {
      toast.error("سن معتبر وارد کنید.");
      return;
    }

    setInteractiveLoading(true);
    try {
      const res = await fetch(`${API_BASE_URL}/api/vania/esanj/attempts/`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...getAuthHeaders(),
          ...(selectedDoctorId ? { "X-Target-Expert-ID": String(selectedDoctorId), "X-Target-Doctor-ID": String(selectedDoctorId) } : {}),
          ...(selectedCaseId ? { "X-Target-Case-ID": String(selectedCaseId) } : {}),
        },
        body: JSON.stringify({
          test_id: activeInteractiveTest.interactive_test_id,
          clinical_test_id: activeInteractiveTest.id,
          doctor_id: selectedDoctorId,
          case_id: selectedCaseId,
          age: parsedAge,
          sex,
          delivery_mode: "json",
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        if (res.status === 402 && body?.payment_required) {
          setPaymentRequest(body as TestPaymentRequest);
          toast.info("برای شروع تست، پرداخت را تکمیل کنید.");
          return;
        }
        throw new Error(body?.error || "شروع تست تعاملی ناموفق بود.");
      }
      const attempt = await res.json() as InteractiveAttempt;
      setActiveAttempt(attempt);
      setPaymentRequest(null);
      setQuestionIndex(0);
      await refreshTests();
    } catch (e: any) {
      toast.error(e.message || "شروع تست تعاملی ناموفق بود.");
    } finally {
      setInteractiveLoading(false);
    }
  };

  const saveInteractiveAnswer = async (question: InteractiveQuestion, value: string) => {
    if (!activeAttempt) return;
    const nextAnswers = { ...(activeAttempt.answers || {}), [String(question.row)]: value };
    setActiveAttempt({ ...activeAttempt, answers: nextAnswers });
    setSavingAnswerValue(`${question.row}-${value}`);
    try {
      const res = await fetch(`${API_BASE_URL}/api/vania/esanj/attempts/${activeAttempt.id}/`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json", ...getAuthHeaders() },
        body: JSON.stringify({ answers: { [String(question.row)]: value } }),
      });
      if (!res.ok) throw new Error("ذخیره پاسخ ناموفق بود.");
      const updated = await res.json() as InteractiveAttempt;
      setActiveAttempt(updated);
      if (questionIndex < activeQuestions.length - 1) {
        setQuestionIndex((prev) => prev + 1);
      }
    } catch (e: any) {
      toast.error(e.message || "ذخیره پاسخ ناموفق بود.");
    } finally {
      setSavingAnswerValue(null);
    }
  };

  const submitInteractiveAttempt = async () => {
    if (!activeAttempt) return;
    setInteractiveSubmitting(true);
    try {
      const res = await fetch(`${API_BASE_URL}/api/vania/esanj/attempts/${activeAttempt.id}/submit/`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...getAuthHeaders() },
        body: JSON.stringify({ answers: activeAttempt.answers || {} }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.error || "ثبت نهایی تست ناموفق بود.");
      }
      const completed = await res.json() as InteractiveAttempt;
      setActiveAttempt(completed);
      setQuestionIndex(0);
      await refreshTests();
      toast.success("تست تعاملی با موفقیت تکمیل شد.");
    } catch (e: any) {
      toast.error(e.message || "ثبت نهایی تست ناموفق بود.");
    } finally {
      setInteractiveSubmitting(false);
    }
  };

  const uploadFile = async () => {
    if (!activeTest?.id) return;
    if (selectedFiles.length === 0 && !resultSummary.trim()) {
      toast.error("متن نتیجه یا فایل را ثبت کنید.");
      return;
    }

    setIsUploading(true);
    try {
      let nextTests = [...(tests || [])];
      if (resultSummary.trim() !== (activeTest.result_text || activeTest.result_summary || "").trim()) {
        const updateRes = await fetch(`${API_BASE_URL}/api/vania/tests/${activeTest.id}/`, {
          method: "PUT",
          headers: {
            "Content-Type": "application/json",
            ...getAuthHeaders(),
            ...(selectedDoctorId ? { "X-Target-Expert-ID": String(selectedDoctorId) } : {}),
            ...(selectedDoctorId ? { "X-Target-Doctor-ID": String(selectedDoctorId) } : {}),
            ...(selectedCaseId ? { "X-Target-Case-ID": String(selectedCaseId) } : {}),
          },
          body: JSON.stringify({
            result_text: resultSummary,
            result_summary: resultSummary,
            case_id: selectedCaseId,
          }),
        });
        if (!updateRes.ok) throw new Error("ذخیره متن نتیجه ناموفق بود.");
        const updated = await updateRes.json();
        nextTests = nextTests.map((t) => (t.id === activeTest.id ? updated : t));
      }

      if (selectedFiles.length === 0) {
        onEdit({ tests: nextTests });
        toast.success("نتیجه تست با موفقیت ذخیره شد.");
        setUploadOpen(false);
        return;
      }

      let latestTest = nextTests.find((t) => t.id === activeTest.id) || null;
      for (const file of selectedFiles) {
        const fd = new FormData();
        fd.append("file", file);
        if (selectedCaseId) fd.append("case_id", selectedCaseId);

        const res = await fetch(`${API_BASE_URL}/api/vania/tests/${activeTest.id}/file/`, {
          method: "POST",
          headers: {
            ...getAuthHeaders(),
            ...(selectedDoctorId ? { "X-Target-Expert-ID": String(selectedDoctorId) } : {}),
            ...(selectedDoctorId ? { "X-Target-Doctor-ID": String(selectedDoctorId) } : {}),
            ...(selectedCaseId ? { "X-Target-Case-ID": String(selectedCaseId) } : {}),
          },
          body: fd,
        });

        if (!res.ok) {
          let errorText = `آپلود فایل «${file.name}» ناموفق بود.`;
          try {
            const body = await res.json();
            if (body?.error) errorText = body.error;
          } catch {}
          throw new Error(errorText);
        }

        latestTest = await res.json();
        nextTests = nextTests.map((t) => (t.id === activeTest.id ? latestTest! : t));
      }

      onEdit({ tests: nextTests });
      toast.success("نتیجه تست با موفقیت ثبت شد.");
      setUploadOpen(false);
    } catch (e: any) {
      toast.error(e.message || "خطا در آپلود فایل.");
    } finally {
      setIsUploading(false);
    }
  };

  const downloadTestFile = async (testId: string, attachmentId?: string, fallbackName?: string | null) => {
    try {
      const query = new URLSearchParams();
      if (attachmentId) query.set("attachment_id", attachmentId);
      const res = await fetch(`${API_BASE_URL}/api/vania/tests/${testId}/file/download/${query.toString() ? `?${query.toString()}` : ""}`, {
        method: "GET",
        headers: {
          ...getAuthHeaders(),
          ...(selectedDoctorId ? { "X-Target-Expert-ID": String(selectedDoctorId) } : {}),
          ...(selectedDoctorId ? { "X-Target-Doctor-ID": String(selectedDoctorId) } : {}),
          ...(selectedCaseId ? { "X-Target-Case-ID": String(selectedCaseId) } : {}),
        },
      });
      if (!res.ok) throw new Error("دانلود فایل ناموفق بود.");

      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = fallbackName || "test-result.pdf";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e: any) {
      toast.error(e.message || "خطا در دانلود فایل.");
    }
  };

  if (isLoading && !tests?.length) {
    return (
      <div className="space-y-3 rounded-xl border border-dashed bg-muted/5 p-4">
        <div className="h-4 w-32 rounded bg-muted" />
        <div className="h-14 rounded-lg bg-muted/60" />
        <div className="h-14 rounded-lg bg-muted/40" />
      </div>
    );
  }

  if (!tests?.length) {
    return (
      <div className="rounded-xl border border-dashed bg-muted/5 px-4 py-10 text-center">
        <FlaskConical className="mx-auto mb-3 h-5 w-5 text-muted-foreground" />
        <div className="text-sm font-medium text-foreground">تستی ثبت نشده است</div>
        <div className="mt-1 text-xs text-muted-foreground">{emptyText}</div>
      </div>
    );
  }

  return (
    <div className="space-y-6 pb-10 animate-in fade-in slide-in-from-right-2 duration-300 font-sans">
      <div className="flex flex-wrap items-center justify-between gap-3 px-1">
        <h3 className="text-sm font-bold flex items-center gap-2 text-foreground">
          <FlaskConical className="w-4 h-4 text-primary" />
          {title}
        </h3>
        <div className="flex flex-wrap items-center gap-1.5">
          {testCounts.pending > 0 ? <Badge variant="secondary" className="text-[10px] font-normal">{testCounts.pending} در انتظار</Badge> : null}
          {testCounts.done > 0 ? <Badge variant="outline" className="text-[10px] font-normal">{testCounts.done} تکمیل</Badge> : null}
          {testCounts.interactive > 0 ? <Badge variant="outline" className="text-[10px] font-normal">{testCounts.interactive} تعاملی</Badge> : null}
        </div>
      </div>

      <div className="space-y-2">
        {sortedTests.map((test) => {
          const attachments = getTestAttachments(test);
          const isInteractive = test.source === "interactive";
          const hasResult = testHasResult(test);
          const actionLabel = isInteractive
            ? test.interactive_status === "COMPLETED"
              ? "مشاهده نتیجه"
              : test.interactive_attempt_id
                ? "ادامه تست"
                : "انجام تست"
            : createLabel;

          return (
            <button
              key={test.id}
              type="button"
              onClick={() => openUpload(test)}
              className="flex w-full items-center justify-between gap-3 rounded-xl border bg-card px-4 py-3 text-right shadow-sm transition hover:border-primary/40 hover:bg-primary/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
            >
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="truncate text-sm font-semibold">{test.title}</span>
                  {isInteractive ? <Badge variant="secondary" className="text-[10px]">تست تعاملی</Badge> : null}
                  {test.url ? <Badge variant="outline" className="text-[10px]">لینک</Badge> : null}
                  {attachments.length > 0 ? <Badge variant="outline" className="text-[10px]">{attachments.length} فایل</Badge> : null}
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
                  <Badge variant={hasResult ? "outline" : "secondary"} className="text-[10px]">
                    {isInteractive ? interactiveStatusLabel(test.interactive_status) : hasResult ? "تکمیل شده" : "در انتظار تکمیل"}
                  </Badge>
                  <span>{toJalali(test.created_at)}</span>
                  {test.url ? <span className="inline-flex items-center gap-1"><Link2 className="w-3 h-3" /> لینک</span> : null}
                </div>
              </div>
              <div className="shrink-0">
                <Button
                  size="sm"
                  type="button"
                  variant="outline"
                  className="h-7 text-[11px]"
                  onClick={(event) => {
                    event.stopPropagation();
                    openUpload(test);
                  }}
                >
                  {isInteractive ? <PlayCircle className="w-3.5 h-3.5 ml-1" /> : <Upload className="w-3.5 h-3.5 ml-1" />}
                  {actionLabel}
                </Button>
              </div>
            </button>
          );
        })}
      </div>

      <Dialog open={uploadOpen} onOpenChange={setUploadOpen}>
        <DialogContent dir="rtl" className="w-[calc(100vw-2rem)] max-w-xl">
          <DialogHeader className="text-right">
            <DialogTitle>{createLabel}</DialogTitle>
            <DialogDescription>
              متن نتیجه را ثبت کنید و در صورت نیاز فایل PDF یا تصویر اضافه کنید.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            <div className="grid gap-1.5">
              <Label className="text-xs">متن نتیجه</Label>
              <Textarea
                value={resultSummary}
                onChange={(e) => setResultSummary(e.target.value)}
                className="min-h-[110px]"
              />
            </div>
            <div className="grid gap-1.5">
              <Label className="text-xs">فایل‌های نتیجه (PDF یا تصویر)</Label>
              <Input type="file" multiple accept="application/pdf,image/*" onChange={(e) => setSelectedFiles(Array.from(e.target.files || []))} />
              {selectedFiles.length > 0 && (
                <div className="rounded-lg border border-border/60 bg-muted/10 p-2">
                  <div className="mb-2 text-[10px] text-muted-foreground">{selectedFiles.length} فایل انتخاب شده</div>
                  <div className="grid gap-1">
                    {selectedFiles.map((file) => (
                      <div key={`${file.name}-${file.size}`} className="truncate text-[10px] text-foreground/80">
                        {file.name}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>

          <DialogFooter>
            <Button onClick={uploadFile} disabled={isUploading} className="w-full gap-2 sm:w-auto">
              {isUploading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
              {isUploading ? "در حال آپلود..." : "ثبت فایل"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={interactiveOpen} onOpenChange={(open) => {
        setInteractiveOpen(open);
        if (!open) setPaymentRequest(null);
      }}>
        <DialogContent dir="rtl" className="flex max-h-[88vh] w-[calc(100vw-2rem)] max-w-2xl flex-col overflow-hidden">
          <DialogHeader className="text-right">
            <DialogTitle>{activeInteractiveTest?.title || "تست تعاملی"}</DialogTitle>
            <DialogDescription>
              پاسخ‌ها خودکار ذخیره می‌شوند.
            </DialogDescription>
          </DialogHeader>

          <div className="flex-1 overflow-y-auto pr-1">
            {interactiveLoading ? (
              <div className="flex min-h-48 items-center justify-center text-sm text-muted-foreground">
                <Loader2 className="ml-2 h-4 w-4 animate-spin" />
                در حال آماده‌سازی تست...
              </div>
            ) : !activeAttempt ? (
              <div className="space-y-4">
                <div className="rounded-xl border bg-muted/10 px-3 py-2 text-xs text-muted-foreground">
                  برای شروع، سن و جنسیت را وارد کنید.
                </div>
                {paymentRequest ? (
                  <div className="rounded-xl border bg-primary/5 p-3">
                    <div className="flex items-start gap-2">
                      <CreditCard className="mt-1 h-4 w-4 shrink-0 text-primary" />
                      <div className="min-w-0 space-y-1">
                        <div className="text-sm font-semibold">پرداخت برای شروع تست</div>
                        <p className="text-xs leading-6 text-muted-foreground">
                          مبلغ قابل پرداخت: {formatNumber(paymentRequest.pricing?.total_amount)} تومان
                        </p>
                      </div>
                    </div>
                  </div>
                ) : null}
                <div className="grid gap-1.5">
                  <Label className="text-xs">سن</Label>
                  <Input value={age} onChange={(event) => setAge(event.target.value)} inputMode="numeric" placeholder="مثلا ۳۰" />
                </div>
                <div className="grid gap-2">
                  <Label className="text-xs">جنسیت</Label>
                  <div className="grid grid-cols-2 gap-2">
                    <Button type="button" variant={sex === "female" ? "secondary" : "outline"} onClick={() => setSex("female")}>
                      زن
                    </Button>
                    <Button type="button" variant={sex === "male" ? "secondary" : "outline"} onClick={() => setSex("male")}>
                      مرد
                    </Button>
                  </div>
                </div>
              </div>
            ) : activeAttempt.status === "COMPLETED" ? (
              <InteractiveTestResultView result={activeAttempt.result} />
            ) : activeQuestion ? (
              <div className="space-y-5">
                <div className="space-y-2">
                  <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground">
                    <span>سوال {questionIndex + 1} از {activeQuestions.length}</span>
                    <span>{answeredCount} پاسخ</span>
                  </div>
                  <Progress value={progressPercent} className="h-1.5" />
                </div>

                <div className="rounded-xl border bg-card p-4 shadow-sm">
                  <h4 className="text-sm font-semibold leading-7">{activeQuestion.title}</h4>
                </div>

                <div className="grid gap-2">
                  {activeQuestion.answers.map((answer) => {
                      const answerValue = String(answer.value);
                      const selected =
                        activeAttempt.answers?.[String(activeQuestion.row)] ===
                        answerValue;
                      return (
                        <Button
                          key={`${activeQuestion.row}-${answer.row}-${answer.value}`}
                          type="button"
                          variant={selected ? "secondary" : "outline"}
                          disabled={!!savingAnswerValue}
                          className="h-auto min-h-11 justify-start px-3 py-2 text-right text-xs leading-6 whitespace-normal"
                          onClick={() => saveInteractiveAnswer(activeQuestion, answerValue)}
                        >
                          {savingAnswerValue ===
                          `${activeQuestion.row}-${answerValue}` ? (
                            <Loader2 className="ml-2 h-3.5 w-3.5 animate-spin" />
                          ) : null}
                          {answer.title}
                        </Button>
                      );
                    })}
                </div>

                <div className="flex items-center justify-between gap-2">
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    disabled={questionIndex <= 0}
                    onClick={() => setQuestionIndex((prev) => Math.max(0, prev - 1))}
                  >
                    قبلی
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    disabled={questionIndex >= activeQuestions.length - 1}
                    onClick={() => setQuestionIndex((prev) => Math.min(activeQuestions.length - 1, prev + 1))}
                  >
                    بعدی
                  </Button>
                </div>
              </div>
            ) : (
              <div className="rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">
                سوالی برای این تست دریافت نشد.
              </div>
            )}
          </div>

          <DialogFooter className="border-t border-border/60 pt-4">
            {!activeAttempt && paymentRequest ? (
              <Button
                onClick={() => router.push(paymentRequest.redirect_url)}
                className="w-full gap-2 sm:w-auto"
              >
                <CreditCard className="h-4 w-4" />
                رفتن به پرداخت
              </Button>
            ) : !activeAttempt ? (
              <Button onClick={startInteractiveAttempt} disabled={interactiveLoading} className="w-full gap-2 sm:w-auto">
                {interactiveLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <PlayCircle className="h-4 w-4" />}
                شروع تست
              </Button>
            ) : activeAttempt.status !== "COMPLETED" ? (
              <Button
                onClick={submitInteractiveAttempt}
                disabled={interactiveSubmitting || answeredCount < activeQuestions.length}
                className="w-full gap-2 sm:w-auto"
              >
                {interactiveSubmitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
                پایان تست
              </Button>
            ) : (
              <Button type="button" variant="outline" onClick={() => setInteractiveOpen(false)} className="w-full sm:w-auto">
                بستن
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
