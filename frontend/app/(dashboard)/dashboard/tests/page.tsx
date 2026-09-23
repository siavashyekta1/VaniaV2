"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  Clock3,
  ClipboardList,
  CreditCard,
  History,
  Loader2,
  Search,
} from "lucide-react";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { InteractiveTestResultView } from "@/components/canvas/renderers/shared/InteractiveTestResultView";
import { ApiError, fetcher } from "@/lib/api";
import { cn } from "@/lib/utils";

type EsanjTest = {
  id: number;
  esanj_test_id: number;
  title: string;
  title_employee?: string;
  base_price?: number | null;
  is_available: boolean;
  is_purchased?: boolean;
};

type EsanjAnswer = {
  row: number;
  title: string;
  value: string;
  optionKey?: string;
};

type EsanjQuestion = {
  row: number;
  title: string;
  questionKey?: string;
  answers?:
    | EsanjAnswer[]
    | Record<string, EsanjAnswer | string | number>
    | null;
};

type EsanjAttempt = {
  id: string;
  invoice_id?: string | null;
  esanj_test_id: number;
  test_title: string;
  status: "IN_PROGRESS" | "SUBMITTED" | "COMPLETED" | "FAILED";
  age: number;
  sex: "male" | "female";
  answers: Record<string, string>;
  questionnaire?: {
    delivery_mode?: "html" | "json";
    questions?: EsanjQuestion[];
  };
  progress: {
    answered: number;
    total: number;
  };
  result?: {
    json?: Record<string, any>;
    grading?: Record<string, any>;
  } | null;
  error_message?: string;
  purchased_at?: string | null;
  started_at: string;
  completed_at?: string | null;
};

type PaymentRequest = {
  invoice_id: string;
  redirect_url: string;
  pricing?: {
    total_amount?: string;
    subtotal_amount?: string;
    tax_amount?: string;
    markup_percent?: string;
  };
};

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

function statusLabel(status: EsanjAttempt["status"]) {
  if (status === "COMPLETED") return "تکمیل شده";
  if (status === "FAILED") return "ناموفق";
  if (status === "SUBMITTED") return "ثبت شده";
  return "در حال انجام";
}

function normalizeQuestionAnswers(
  question?: EsanjQuestion | null,
): EsanjAnswer[] {
  const rawAnswers = question?.answers;
  const answerItems = Array.isArray(rawAnswers)
    ? rawAnswers.map((answer, index) => ({
        ...answer,
        optionKey: `index:${index}`,
      }))
    : rawAnswers && typeof rawAnswers === "object"
      ? Object.entries(rawAnswers).map(([key, value], index) => {
          if (value && typeof value === "object") {
            return {
              ...(value as EsanjAnswer),
              row: Number((value as EsanjAnswer).row ?? index + 1),
              value: String((value as EsanjAnswer).value ?? key),
              optionKey: `key:${key}`,
            };
          }
          return {
            row: index + 1,
            title: String(value ?? key),
            value: key,
            optionKey: `key:${key}`,
          };
        })
      : [];

  return answerItems
    .map((answer, index) => ({
      row: Number(answer?.row ?? index + 1),
      title: String(answer?.title ?? answer?.value ?? index + 1),
      value: String(answer?.value ?? answer?.row ?? index + 1),
      optionKey: String(answer?.optionKey ?? `row:${answer?.row ?? index + 1}`),
    }))
    .filter((answer) => answer.title.trim() && answer.value.trim());
}

function getAnswerOptionKey(
  questionRow: number,
  answer: EsanjAnswer,
  index: number,
) {
  return `${questionRow}:${answer.optionKey || answer.row}:${answer.value}:${index}`;
}

function getAnswerSubmitValue(answer: EsanjAnswer) {
  return String(answer.row);
}

function normalizeQuestionRow(value: unknown, fallback: number) {
  const row = Number(value);
  return Number.isFinite(row) ? row : fallback;
}

function normalizeQuestions(value?: EsanjQuestion[] | null): EsanjQuestion[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((question) => question && typeof question === "object")
    .map((question, index) => {
      const answers = normalizeQuestionAnswers(question);
      const row = normalizeQuestionRow(question.row, index + 1);
      return {
        ...question,
        row,
        questionKey: `${index}:${row}`,
        title: String(question.title ?? `سوال ${index + 1}`),
        answers,
      };
    })
    .filter((question) => normalizeQuestionAnswers(question).length > 0);
}

export default function EsanjTestsPage() {
  const router = useRouter();
  const [tests, setTests] = useState<EsanjTest[]>([]);
  const [attempts, setAttempts] = useState<EsanjAttempt[]>([]);
  const [selectedTest, setSelectedTest] = useState<EsanjTest | null>(null);
  const [startModalOpen, setStartModalOpen] = useState(false);
  const [historyModalOpen, setHistoryModalOpen] = useState(false);
  const [activeAttempt, setActiveAttempt] = useState<EsanjAttempt | null>(null);
  const [query, setQuery] = useState("");
  const [age, setAge] = useState("25");
  const [sex, setSex] = useState<"male" | "female">("female");
  const [questionIndex, setQuestionIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [savingAnswer, setSavingAnswer] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [paymentRequest, setPaymentRequest] = useState<PaymentRequest | null>(
    null,
  );
  const [selectedAnswerKeys, setSelectedAnswerKeys] = useState<
    Record<string, number>
  >({});
  const saveAnswerRequestId = useRef(0);

  const questions = useMemo(
    () => normalizeQuestions(activeAttempt?.questionnaire?.questions),
    [activeAttempt?.questionnaire?.questions],
  );
  const currentQuestion = questions[questionIndex];
  const currentAnswers = useMemo(
    () => normalizeQuestionAnswers(currentQuestion),
    [currentQuestion],
  );
  const answered = activeAttempt
    ? Object.keys(activeAttempt.answers || {}).length
    : 0;
  const progressValue = questions.length
    ? Math.round((answered / questions.length) * 100)
    : 0;

  const filteredTests = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return tests;
    return tests.filter((test) => {
      return (
        test.title.toLowerCase().includes(normalized) ||
        String(test.esanj_test_id).includes(normalized) ||
        (test.title_employee || "").toLowerCase().includes(normalized)
      );
    });
  }, [query, tests]);

  const inProgressAttempts = useMemo(
    () => attempts.filter((attempt) => attempt.status === "IN_PROGRESS"),
    [attempts],
  );

  const purchasedEntries = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    const attemptMatches = (attempt: EsanjAttempt) =>
      !normalized ||
      attempt.test_title.toLowerCase().includes(normalized) ||
      String(attempt.esanj_test_id).includes(normalized);

    return [
      ...attempts
        .filter(attemptMatches)
        .map((attempt) => ({ type: "attempt" as const, attempt })),
      ...filteredTests
        .filter((test) => test.is_purchased)
        .map((test) => ({ type: "unused" as const, test })),
    ].sort((a, b) => {
      const aDate =
        a.type === "attempt"
          ? a.attempt.purchased_at || a.attempt.started_at
          : "";
      const bDate =
        b.type === "attempt"
          ? b.attempt.purchased_at || b.attempt.started_at
          : "";
      return new Date(bDate || 0).getTime() - new Date(aDate || 0).getTime();
    });
  }, [attempts, filteredTests, query]);

  const availableTests = useMemo(() => filteredTests, [filteredTests]);

  const loadData = useCallback(
    async (options?: { clearError?: boolean; showLoading?: boolean }) => {
      if (options?.showLoading !== false) {
        setLoading(true);
      }
      if (options?.clearError !== false) {
        setError(null);
      }
      try {
        const [catalog, history] = await Promise.all([
          fetcher<{ tests: EsanjTest[] }>("/api/vania/esanj/tests/"),
          fetcher<{ attempts: EsanjAttempt[] }>("/api/vania/esanj/attempts/"),
        ]);
        setTests(catalog.tests || []);
        setAttempts(history.attempts || []);
      } catch (err: any) {
        setError(err?.message || "دریافت فهرست آزمون‌ها ناموفق بود.");
      } finally {
        if (options?.showLoading !== false) {
          setLoading(false);
        }
      }
    },
    [],
  );

  useEffect(() => {
    loadData();
  }, [loadData]);

  const startAttempt = async () => {
    if (!selectedTest) return;
    const parsedAge = Number(age);
    if (!Number.isFinite(parsedAge) || parsedAge < 1 || parsedAge > 150) {
      toast.error("سن را به درستی وارد کنید");
      return;
    }

    setStarting(true);
    setError(null);
    try {
      const attempt = await fetcher<EsanjAttempt>(
        "/api/vania/esanj/attempts/",
        {
          method: "POST",
          body: JSON.stringify({
            test_id: selectedTest.esanj_test_id,
            age: parsedAge,
            sex,
            delivery_mode: "json",
          }),
        },
      );
      setActiveAttempt(attempt);
      setQuestionIndex(0);
      setSelectedTest(null);
      setStartModalOpen(false);
      setAttempts((prev) => [
        attempt,
        ...prev.filter((item) => item.id !== attempt.id),
      ]);
    } catch (err: any) {
      if (
        err instanceof ApiError &&
        err.status === 402 &&
        err.detail?.payment_required
      ) {
        setPaymentRequest(err.detail as PaymentRequest);
        toast.info("برای شروع آزمون، پرداخت را تکمیل کنید.");
        return;
      }
      setError(err?.message || "شروع آزمون ناموفق بود.");
    } finally {
      setStarting(false);
    }
  };

  const selectTestForStart = useCallback((test: EsanjTest) => {
    setSelectedTest(test);
    setPaymentRequest(null);
    setStartModalOpen(true);
  }, []);

  const saveAnswer = async (
    questionRow: number,
    answerValue: string,
    questionKey: string,
    answerIndex: number,
  ) => {
    if (!activeAttempt || activeAttempt.status !== "IN_PROGRESS") return;
    const requestId = saveAnswerRequestId.current + 1;
    saveAnswerRequestId.current = requestId;

    setSelectedAnswerKeys((prev) => ({
      ...prev,
      [questionKey]: answerIndex,
    }));
    const nextAttempt = {
      ...activeAttempt,
      answers: {
        ...(activeAttempt.answers || {}),
        [String(questionRow)]: answerValue,
      },
    };
    setActiveAttempt(nextAttempt);
    setSavingAnswer(true);

    try {
      const updated = await fetcher<EsanjAttempt>(
        `/api/vania/esanj/attempts/${activeAttempt.id}/`,
        {
          method: "PATCH",
          body: JSON.stringify({ answers: { [questionRow]: answerValue } }),
        },
      );
      if (saveAnswerRequestId.current !== requestId) return;
      setActiveAttempt(updated);
      setAttempts((prev) =>
        prev.map((item) => (item.id === updated.id ? updated : item)),
      );
    } catch (err: any) {
      toast.error(err?.message || "ذخیره پاسخ انجام نشد");
      setSelectedAnswerKeys((prev) => {
        const next = { ...prev };
        delete next[questionKey];
        return next;
      });
    } finally {
      setSavingAnswer(false);
    }
  };

  const submitAttempt = async () => {
    if (!activeAttempt) return;
    if (answered < questions.length) {
      toast.error("برای دریافت نتیجه، همه سوال‌ها را پاسخ دهید");
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      const completed = await fetcher<EsanjAttempt>(
        `/api/vania/esanj/attempts/${activeAttempt.id}/submit/`,
        {
          method: "POST",
          body: JSON.stringify({ answers: activeAttempt.answers || {} }),
        },
      );
      setActiveAttempt(completed);
      setQuestionIndex(0);
      setAttempts((prev) => [
        completed,
        ...prev.filter((item) => item.id !== completed.id),
      ]);
      setTests((prev) =>
        prev.map((test) =>
          test.esanj_test_id === completed.esanj_test_id
            ? { ...test, is_purchased: false }
            : test,
        ),
      );
      await loadData({ clearError: false, showLoading: false });
      toast.success("نتیجه آزمون آماده شد");
    } catch (err: any) {
      setError(err?.message || "ثبت آزمون ناموفق بود.");
      toast.error(err?.message || "ثبت آزمون ناموفق بود.");
      await loadData({ clearError: false });
    } finally {
      setSubmitting(false);
    }
  };

  const openAttempt = async (attempt: EsanjAttempt) => {
    setError(null);
    try {
      const full = await fetcher<EsanjAttempt>(
        `/api/vania/esanj/attempts/${attempt.id}/`,
      );
      setActiveAttempt(full);
      setSelectedTest(null);
      setHistoryModalOpen(false);
      const firstUnanswered = normalizeQuestions(
        full.questionnaire?.questions,
      ).findIndex((q) => !full.answers?.[String(q.row)]);
      setQuestionIndex(firstUnanswered >= 0 ? firstUnanswered : 0);
    } catch (err: any) {
      setError(err?.message || "دریافت سابقه آزمون ناموفق بود.");
    }
  };

  const renderStartSettings = () => (
    <div className="space-y-4">
      {selectedTest ? (
        <>
          <div className="rounded-lg border bg-muted/20 px-3 py-2 text-sm leading-7 font-medium">
            {selectedTest.title}
          </div>
          <div className="grid gap-2">
            <Label htmlFor="esanj-age">سن</Label>
            <Input
              id="esanj-age"
              inputMode="numeric"
              value={age}
              onChange={(event) => setAge(event.target.value)}
            />
          </div>
          <div className="grid gap-2">
            <Label>جنسیت</Label>
            <div className="grid grid-cols-2 gap-2">
              <Button
                type="button"
                variant={sex === "female" ? "default" : "outline"}
                onClick={() => setSex("female")}
              >
                زن
              </Button>
              <Button
                type="button"
                variant={sex === "male" ? "default" : "outline"}
                onClick={() => setSex("male")}
              >
                مرد
              </Button>
            </div>
          </div>
          {paymentRequest && (
            <div className="rounded-lg border bg-primary/5 p-3">
              <div className="flex items-start gap-2">
                <CreditCard className="mt-1 h-4 w-4 shrink-0 text-primary" />
                <div className="min-w-0 space-y-1">
                  <div className="text-sm font-semibold">
                    پرداخت برای شروع آزمون
                  </div>
                  <p className="text-xs leading-6 text-muted-foreground">
                    مبلغ قابل پرداخت:{" "}
                    {formatNumber(paymentRequest.pricing?.total_amount)} تومان
                  </p>
                </div>
              </div>
            </div>
          )}
        </>
      ) : (
        <p className="text-sm leading-7 text-muted-foreground">
          یک آزمون را از فهرست انتخاب کنید.
        </p>
      )}
    </div>
  );

  if (loading) {
    return (
      <div className="flex h-[50vh] w-full items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div
      className="mx-auto flex h-full w-full max-w-6xl flex-col gap-5 pt-4 pb-8"
      dir="rtl"
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div className="space-y-1">
          <h1 className="flex items-center gap-2 text-xl font-bold tracking-tight sm:text-2xl">
            <ClipboardList className="h-5 w-5 text-primary" />
            تست‌ها
          </h1>
          <p className="max-w-2xl text-sm leading-7 text-muted-foreground">
            آزمون را انتخاب کنید، پاسخ دهید و نتیجه را همین‌جا ببینید.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {savingAnswer && (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              در حال ذخیره پاسخ
            </div>
          )}
          <Button
            variant="outline"
            className="gap-2"
            onClick={() => setHistoryModalOpen(true)}
          >
            <History className="h-4 w-4" />
            تاریخچه من
          </Button>
        </div>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>خطا</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {!activeAttempt && inProgressAttempts.length > 0 && (
        <div className="flex flex-col gap-3 rounded-lg border bg-muted/20 p-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex min-w-0 items-start gap-3">
            <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
              <Clock3 className="h-4 w-4" />
            </div>
            <div className="min-w-0">
              <h2 className="text-sm font-semibold">آزمون نیمه‌تمام دارید</h2>
              <p className="mt-1 line-clamp-2 text-sm leading-7 text-muted-foreground">
                {inProgressAttempts[0].test_title}
              </p>
            </div>
          </div>
          <Button
            variant="outline"
            onClick={() => openAttempt(inProgressAttempts[0])}
          >
            ادامه آزمون
          </Button>
        </div>
      )}

      {activeAttempt ? (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_300px]">
          <section className="min-w-0 rounded-lg border bg-background p-4 shadow-sm">
            <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <h2 className="text-lg font-semibold">
                    {activeAttempt.test_title}
                  </h2>
                  <Badge
                    variant={
                      activeAttempt.status === "COMPLETED"
                        ? "default"
                        : "secondary"
                    }
                  >
                    {statusLabel(activeAttempt.status)}
                  </Badge>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  {formatNumber(answered)} از {formatNumber(questions.length)}{" "}
                  پاسخ
                </p>
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setActiveAttempt(null)}
              >
                بازگشت به فهرست
              </Button>
            </div>

            <Progress value={progressValue} className="mb-5 h-2" />

            {activeAttempt.status === "COMPLETED" ||
            activeAttempt.status === "FAILED" ? (
              <div className="space-y-4">
                {activeAttempt.status === "FAILED" && (
                  <Alert variant="destructive">
                    <AlertCircle className="h-4 w-4" />
                    <AlertTitle>ثبت آزمون ناموفق بود</AlertTitle>
                    <AlertDescription>
                      {activeAttempt.error_message ||
                        "خطایی از سمت سرویس آزمون دریافت شد."}
                    </AlertDescription>
                  </Alert>
                )}
                <InteractiveTestResultView result={activeAttempt.result} />
              </div>
            ) : currentQuestion ? (
              <div className="space-y-5">
                <div className="rounded-md bg-muted/25 p-4">
                  <div className="mb-2 text-xs font-medium text-muted-foreground">
                    سوال {formatNumber(questionIndex + 1)} از{" "}
                    {formatNumber(questions.length)}
                  </div>
                  <h2 className="text-base leading-8 font-semibold">
                    {currentQuestion.title}
                  </h2>
                </div>

                {currentAnswers.length > 0 ? (
                  <div className="grid gap-2">
                    {currentAnswers.map((answer, answerIndex) => {
                      const questionKey =
                        currentQuestion.questionKey ??
                        `${questionIndex}:${currentQuestion.row}`;
                      const answerOptionKey = getAnswerOptionKey(
                        currentQuestion.row,
                        answer,
                        answerIndex,
                      );
                      const storedAnswer =
                        activeAttempt.answers?.[String(currentQuestion.row)];
                      const localSelectedIndex =
                        selectedAnswerKeys[questionKey];
                      const selected =
                        localSelectedIndex !== undefined
                          ? localSelectedIndex === answerIndex
                          : String(storedAnswer) ===
                            getAnswerSubmitValue(answer);
                      return (
                        <button
                          key={answerOptionKey}
                          type="button"
                          onClick={() =>
                            saveAnswer(
                              currentQuestion.row,
                              getAnswerSubmitValue(answer),
                              questionKey,
                              answerIndex,
                            )
                          }
                          className={cn(
                            "flex min-h-12 w-full items-center justify-between rounded-md border px-4 py-3 text-right text-sm transition-colors",
                            "hover:border-primary/50 hover:bg-primary/5 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
                            selected
                              ? "border-primary bg-primary/10 text-primary"
                              : "bg-background",
                          )}
                        >
                          <span className="leading-7">{answer.title}</span>
                          {selected && (
                            <CheckCircle2 className="h-4 w-4 shrink-0" />
                          )}
                        </button>
                      );
                    })}
                  </div>
                ) : (
                  <Alert variant="destructive">
                    <AlertCircle className="h-4 w-4" />
                    <AlertTitle>گزینه‌های سوال دریافت نشد</AlertTitle>
                    <AlertDescription>
                      ساختار گزینه‌های این سوال از سرویس آزمون قابل نمایش نیست.
                    </AlertDescription>
                  </Alert>
                )}

                <div className="flex flex-col-reverse gap-2 sm:flex-row sm:items-center sm:justify-between">
                  <Button
                    variant="outline"
                    onClick={() =>
                      setQuestionIndex((value) => Math.max(0, value - 1))
                    }
                    disabled={questionIndex === 0}
                  >
                    <ArrowRight className="h-4 w-4" />
                    قبلی
                  </Button>
                  {questionIndex >= questions.length - 1 ? (
                    <Button
                      onClick={submitAttempt}
                      disabled={submitting || answered < questions.length}
                    >
                      {submitting ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <CheckCircle2 className="h-4 w-4" />
                      )}
                      دریافت نتیجه
                    </Button>
                  ) : (
                    <Button
                      onClick={() =>
                        setQuestionIndex((value) =>
                          Math.min(questions.length - 1, value + 1),
                        )
                      }
                    >
                      بعدی
                      <ArrowLeft className="h-4 w-4" />
                    </Button>
                  )}
                </div>
              </div>
            ) : (
              <Alert>
                <AlertCircle className="h-4 w-4" />
                <AlertTitle>پرسشنامه خالی است</AlertTitle>
                <AlertDescription>
                  برای این آزمون سوالی دریافت نشد.
                </AlertDescription>
              </Alert>
            )}
          </section>

          <aside className="space-y-3 rounded-lg border bg-muted/15 p-4">
            <h3 className="text-sm font-semibold">مرور پاسخ‌ها</h3>
            <div className="grid grid-cols-6 gap-2 lg:grid-cols-5">
              {questions.map((question, index) => {
                const isAnswered = Boolean(
                  activeAttempt.answers?.[String(question.row)],
                );
                return (
                  <button
                    key={question.row}
                    type="button"
                    onClick={() => setQuestionIndex(index)}
                    className={cn(
                      "flex aspect-square items-center justify-center rounded-md border text-xs transition-colors",
                      index === questionIndex && "border-primary text-primary",
                      isAnswered ? "bg-primary/10" : "bg-background",
                    )}
                  >
                    {formatNumber(index + 1)}
                  </button>
                );
              })}
            </div>
          </aside>
        </div>
      ) : (
        <div className="space-y-3">
          <section className="min-w-0 space-y-3">
            <div className="relative">
              <Search className="pointer-events-none absolute top-1/2 right-3 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                dir="rtl"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="جستجوی نام آزمون یا شناسه"
                className="pr-9 text-right"
              />
            </div>

            {!filteredTests.length ? (
              <div className="rounded-lg border bg-muted/20 p-8 text-center text-sm leading-7 text-muted-foreground">
                آزمون فعالی برای حساب شما پیدا نشد.
              </div>
            ) : (
              <div className="space-y-5">
                {purchasedEntries.length > 0 && (
                  <section className="space-y-2">
                    <div className="flex items-center justify-between gap-3 text-right">
                      <h2 className="flex items-center justify-end gap-2 text-right text-sm font-semibold">
                        <CheckCircle2 className="h-4 w-4 text-primary" />
                        آزمون‌های خریداری‌شده
                      </h2>
                      <Badge variant="secondary">
                        {formatNumber(purchasedEntries.length)} مورد
                      </Badge>
                    </div>
                    <div className="grid gap-3">
                      {purchasedEntries.map((entry) => {
                        if (entry.type === "unused") {
                          const test = entry.test;
                          return (
                            <Card
                              key={`unused-${test.esanj_test_id}`}
                              className="rounded-lg border-primary/30 bg-primary/5"
                            >
                              <CardContent className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
                                <div className="min-w-0 space-y-1 text-right">
                                  <div className="flex flex-wrap items-center justify-end gap-2 text-right">
                                    <h3 className="leading-7 font-semibold">
                                      {test.title}
                                    </h3>
                                    <Badge variant="default">خریداری شده</Badge>
                                    <Badge variant="outline">
                                      #{formatNumber(test.esanj_test_id)}
                                    </Badge>
                                  </div>
                                  <p className="text-xs leading-6 text-muted-foreground">
                                    این خرید هنوز استفاده نشده و آماده شروع
                                    آزمون است.
                                  </p>
                                </div>
                                <Button
                                  onClick={() => selectTestForStart(test)}
                                >
                                  شروع آزمون
                                </Button>
                              </CardContent>
                            </Card>
                          );
                        }

                        const attempt = entry.attempt;
                        const isDone =
                          attempt.status === "COMPLETED" ||
                          attempt.status === "FAILED";
                        return (
                          <Card
                            key={`attempt-${attempt.id}`}
                            className="rounded-lg border-primary/30 bg-primary/5"
                          >
                            <CardContent className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
                              <div className="min-w-0 space-y-1 text-right">
                                <div className="flex flex-wrap items-center justify-end gap-2 text-right">
                                  <h3 className="leading-7 font-semibold">
                                    {attempt.test_title}
                                  </h3>
                                  <Badge
                                    variant={
                                      attempt.status === "COMPLETED"
                                        ? "default"
                                        : "secondary"
                                    }
                                  >
                                    {statusLabel(attempt.status)}
                                  </Badge>
                                  <Badge variant="outline">
                                    #{formatNumber(attempt.esanj_test_id)}
                                  </Badge>
                                </div>
                                <p className="text-xs leading-6 text-muted-foreground">
                                  تاریخ خرید:{" "}
                                  {formatDate(
                                    attempt.purchased_at || attempt.started_at,
                                  )}
                                  {attempt.completed_at
                                    ? ` · تاریخ انجام: ${formatDate(attempt.completed_at)}`
                                    : ""}
                                </p>
                              </div>
                              <Button onClick={() => openAttempt(attempt)}>
                                {isDone ? "نتیجه تست" : "ادامه آزمون"}
                              </Button>
                            </CardContent>
                          </Card>
                        );
                      })}
                    </div>
                  </section>
                )}

                {availableTests.length > 0 && (
                  <section className="space-y-2">
                    {purchasedEntries.length > 0 && (
                      <h2 className="text-right text-sm font-semibold">
                        همه آزمون‌ها
                      </h2>
                    )}
                    <div className="grid gap-3">
                      {availableTests.map((test) => (
                        <Card key={test.esanj_test_id} className="rounded-lg">
                          <CardContent className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
                            <div className="min-w-0 space-y-1 text-right">
                              <div className="flex flex-wrap items-center justify-end gap-2 text-right">
                                <h3 className="leading-7 font-semibold">
                                  {test.title}
                                </h3>
                                <Badge variant="outline">
                                  #{formatNumber(test.esanj_test_id)}
                                </Badge>
                              </div>
                              {test.base_price != null && (
                                <p className="text-xs text-muted-foreground">
                                  قیمت پایه: {formatNumber(test.base_price)}{" "}
                                  تومان
                                </p>
                              )}
                            </div>
                            <Button onClick={() => selectTestForStart(test)}>
                              شروع آزمون
                            </Button>
                          </CardContent>
                        </Card>
                      ))}
                    </div>
                  </section>
                )}
              </div>
            )}
          </section>
        </div>
      )}

      <Dialog
        open={startModalOpen}
        onOpenChange={(open) => {
          setStartModalOpen(open);
          if (!open) {
            setSelectedTest(null);
            setPaymentRequest(null);
          }
        }}
      >
        <DialogContent dir="rtl" className="w-[calc(100vw-2rem)] max-w-md">
          <DialogHeader className="text-right">
            <DialogTitle>شروع آزمون</DialogTitle>
            <DialogDescription>تنظیمات آزمون را بررسی کنید.</DialogDescription>
          </DialogHeader>
          {renderStartSettings()}
          <DialogFooter>
            {paymentRequest ? (
              <Button
                className="w-full gap-2"
                onClick={() => router.push(paymentRequest.redirect_url)}
              >
                <CreditCard className="h-4 w-4" />
                رفتن به پرداخت
              </Button>
            ) : (
              <Button
                className="w-full gap-2"
                onClick={startAttempt}
                disabled={starting || !selectedTest}
              >
                {starting ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <ClipboardList className="h-4 w-4" />
                )}
                شروع آزمون
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={historyModalOpen} onOpenChange={setHistoryModalOpen}>
        <DialogContent
          dir="rtl"
          className="flex max-h-[85vh] w-[calc(100vw-2rem)] max-w-2xl flex-col overflow-hidden"
        >
          <DialogHeader className="text-right">
            <DialogTitle>تاریخچه من</DialogTitle>
            <DialogDescription>
              {attempts.length
                ? `${formatNumber(attempts.length)} آزمون ذخیره شده`
                : "هنوز آزمونی شروع نکرده‌اید."}
            </DialogDescription>
          </DialogHeader>
          <div className="flex-1 overflow-y-auto pr-1">
            {!attempts.length ? (
              <div className="rounded-lg border border-dashed bg-muted/10 px-4 py-10 text-center text-sm text-muted-foreground">
                تاریخچه‌ای برای نمایش وجود ندارد.
              </div>
            ) : (
              <div className="space-y-2">
                {attempts.map((attempt) => (
                  <button
                    key={attempt.id}
                    type="button"
                    onClick={() => openAttempt(attempt)}
                    className="w-full rounded-lg border bg-muted/10 p-3 text-right transition-colors hover:border-primary/50 hover:bg-primary/5 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <span className="line-clamp-2 text-sm leading-6 font-medium">
                        {attempt.test_title}
                      </span>
                      <Badge
                        variant={
                          attempt.status === "COMPLETED"
                            ? "default"
                            : "secondary"
                        }
                        className="shrink-0"
                      >
                        {statusLabel(attempt.status)}
                      </Badge>
                    </div>
                    <div className="mt-1 text-xs text-muted-foreground">
                      {attempt.completed_at
                        ? formatDate(attempt.completed_at)
                        : formatDate(attempt.started_at)}
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
