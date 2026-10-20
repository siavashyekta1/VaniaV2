"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter, useSearchParams, useParams } from "next/navigation";
import { 
  Loader2, Check, Copy, ArrowRight, 
  CreditCard, Calendar, Hash, FileText, Tag, User, Building2, Clock, Sparkles
} from "lucide-react";
import { toast } from "sonner";

// --- UI Components ---
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardFooter } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";

// --- Custom Components ---
import { ManualPaymentForm } from "@/components/billing/ManualPaymentForm";

// --- Libs & Utilities ---
import { API_BASE_URL, getAuthHeaders, ApiError } from "@/lib/api";
import { cn, formatExactCurrency as formatCurrency } from "@/lib/utils";
import { Invoice } from "@/lib/types";
import { APP_CONFIG } from "@/lib/config";

const ENABLE_ONLINE_PAYMENT = true;

export default function InvoiceDetailPage() {
  const params = useParams();
  const id = params.id as string;
  const router = useRouter();
  const searchParams = useSearchParams();
  
  // --- State ---
  const [invoice, setInvoice] = useState<Invoice | null>(null);
  const [loading, setLoading] = useState(true);
  
  // Online Payment State
  const [paying, setPaying] = useState(false);
  
  // Discount State
  const [discountCode, setDiscountCode] = useState("");
  const [isApplyingDiscount, setIsApplyingDiscount] = useState(false);

  // --- Fetch Invoice ---
  const fetchInvoice = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE_URL}/api/billing/invoices/${id}/`, {
        headers: getAuthHeaders(),
      });
      if (!res.ok) throw new Error("Invoice not found");
      const data = await res.json();
      setInvoice(data);
    } catch (err: any) {
      toast.error(err.message || "خطا در دریافت فاکتور");
      router.push('/dashboard/invoices');
    } finally {
      setLoading(false);
    }
  }, [id, router]);

  useEffect(() => {
    fetchInvoice();
  }, [fetchInvoice]);

  // --- Handle Callback Query Params (for Online Payment) ---
  useEffect(() => {
    const statusParam = searchParams.get('status');
    if (statusParam === 'success') toast.success("پرداخت با موفقیت انجام شد.");
    if (statusParam === 'failed') toast.error("پرداخت ناموفق بود.");
  }, [searchParams]);

  // --- Actions ---

  const handleApplyDiscount = async () => {
    if (!discountCode || !id) return;
    setIsApplyingDiscount(true);
    try {
      const res = await fetch(`${API_BASE_URL}/api/billing/invoices/${id}/apply_discount/`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...getAuthHeaders() },
        body: JSON.stringify({ code: discountCode })
      });
      const data = await res.json();
      if (!res.ok) throw new ApiError(data.error, res.status);
      toast.success("کد تخفیف اعمال شد!");
      setInvoice(prev => prev ? {
        ...prev,
        total_amount: data.new_total,
        tax_amount: data.tax_amount,
        subtotal_amount: data.subtotal_amount,
        discount_amount: data.discount_amount,
      } : null);
      setDiscountCode("");
    } catch (err: any) {
      toast.error(err.message || "کد تخفیف نامعتبر است.");
    } finally {
      setIsApplyingDiscount(false);
    }
  };
  
  const handleOnlinePayment = async () => {
    if (!id) return;
    setPaying(true);
    try {
      const res = await fetch(`${API_BASE_URL}/api/billing/pay/${id}/`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...getAuthHeaders() },
        body: JSON.stringify({}),
      });
      const data = await res.json();
      if (!res.ok) throw new ApiError(data.error, res.status);
      
      if (data.status === "paid") {
        toast.success("سفارش با موفقیت فعال شد.");
        await fetchInvoice();
      } else {
        const gatewayUrl = data.action_url || data.redirect_url;
        if (data.status === "gateway_ready" && gatewayUrl) {
          window.location.href = gatewayUrl;
          return;
        }
        throw new Error("لینک درگاه پرداخت دریافت نشد.");
      }
    } catch (e: any) {
      toast.error(e.message || "خطا در اتصال به درگاه.");
    } finally {
      setPaying(false);
    }
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    toast.success("کپی شد");
  };

  // --- Render Helpers ---

  if (loading) return <div className="flex h-[60vh] items-center justify-center"><Loader2 className="animate-spin text-primary h-10 w-10" /></div>;
  if (!invoice) return null;

  const isPaid = invoice.status === "PAID";
  // @ts-ignore
  const isWaiting = invoice.status === "WAITING" || invoice.status === "WAITING_APPROVAL";
  const itemTitle = invoice.item_name || "محصول نامشخص";
  const itemDesc = invoice.item_description || "خرید اعتبار یا سرویس ویژه";
  const subtotalAmount = parseFloat(invoice.subtotal_amount || "0");
  const discountAmount = parseFloat(invoice.discount_amount || "0");
  const taxAmount = parseFloat(invoice.tax_amount || "0");
  const taxRate = parseFloat(invoice.tax_rate || "10");
  const itemAmount = subtotalAmount || parseFloat(invoice.total_amount) + discountAmount - taxAmount;
  const payableAmount = parseFloat(invoice.total_amount);

  const getStatusBadge = () => {
    if (isPaid) return <Badge className="bg-emerald-500 hover:bg-emerald-600 gap-1.5"><Check className="w-3.5 h-3.5" /> پرداخت شده</Badge>;
    if (isWaiting) return <Badge className="bg-blue-500 hover:bg-blue-600 gap-1.5"><Clock className="w-3.5 h-3.5" /> در انتظار تایید</Badge>;
    if (invoice.status === "CANCELLED") return <Badge variant="destructive">لغو شده</Badge>;
    return <Badge variant="outline" className="border-amber-500 text-amber-600 bg-amber-50 gap-1.5"><CreditCard className="w-3.5 h-3.5" /> در انتظار پرداخت</Badge>;
  };

  return (
    <div className="flex flex-col items-center justify-center min-h-[80vh] p-4 animate-in fade-in zoom-in-95 duration-500" dir="rtl">
      
      {/* Back Link */}
      <div className="w-full max-w-5xl mb-6 flex justify-start">
        <Button variant="ghost" className="text-muted-foreground hover:text-foreground gap-2 pl-0" onClick={() => router.push('/dashboard/invoices')}>
            <ArrowRight className="h-4 w-4" /> لیست سفارشات
        </Button>
      </div>

      <div className="w-full max-w-5xl grid grid-cols-1 md:grid-cols-5 gap-6">
        
        {/* --- LEFT: MAIN INVOICE TICKET --- */}
        <Card className="md:col-span-3 border-none shadow-2xl overflow-hidden relative bg-card">
            {/* Top Decoration */}
            <div className={cn(
                "h-2 w-full", 
                isPaid ? "bg-emerald-500" : isWaiting ? "bg-blue-500" : "bg-amber-500"
            )} />
            
            <CardContent className="p-8 space-y-8">
                
                {/* Header */}
                <div className="flex justify-between items-start">
                    <div className="flex gap-4">
                        <div className="h-12 w-12 rounded-xl bg-primary/10 flex items-center justify-center text-primary">
                            <FileText className="h-6 w-6" />
                        </div>
                        <div>
                            <h1 className="text-2xl font-bold tracking-tight">فاکتور خرید</h1>
                            <div className="flex items-center gap-2 text-sm text-muted-foreground mt-1 cursor-pointer hover:text-primary transition-colors" onClick={() => copyToClipboard(invoice.id)}>
                                <span className="font-mono">{invoice.id.slice(0, 8)}...</span>
                                <Copy className="h-3 w-3" />
                            </div>
                        </div>
                    </div>
                    
                    {getStatusBadge()}
                </div>

                {/* Details Grid */}
                <div className="grid grid-cols-2 gap-8 p-6 bg-muted/30 rounded-2xl border border-border/50">
                    <div className="space-y-4">
                        <div className="space-y-1">
                            <span className="text-xs text-muted-foreground flex items-center gap-1.5">
                                <User className="h-3.5 w-3.5" /> خریدار
                            </span>
                            <p className="font-medium text-sm">{invoice.user_name}</p>
                            <p className="text-xs font-mono text-muted-foreground">{invoice.user_phone}</p>
                        </div>
                        <div className="space-y-1">
                            <span className="text-xs text-muted-foreground flex items-center gap-1.5">
                                <Calendar className="h-3.5 w-3.5" /> تاریخ صدور
                            </span>
                            <p className="font-medium text-sm font-mono">{new Date(invoice.created_at).toLocaleDateString('fa-IR')}</p>
                        </div>
                    </div>

                    <div className="space-y-4 text-left" dir="ltr">
                        <div className="space-y-1">
                            <span className="text-xs text-muted-foreground flex items-center gap-1.5 justify-end">
                                صادر کننده <Building2 className="h-3.5 w-3.5" />
                            </span>
                            <p className="font-medium text-sm text-right">{APP_CONFIG.BRANDING.COMPANY_NAME}</p>
                        </div>
                        {invoice.transaction_ref_id && (
                            <div className="space-y-1">
                                <span className="text-xs text-muted-foreground flex items-center gap-1.5 justify-end">
                                    شماره پیگیری <Hash className="h-3.5 w-3.5" />
                                </span>
                                <p className={cn("font-mono text-sm text-right", isPaid ? "text-emerald-600 font-bold" : "text-muted-foreground")}>
                                    {invoice.transaction_ref_id}
                                </p>
                            </div>
                        )}
                    </div>
                </div>

                {/* Line Item */}
                <div>
                    <h3 className="text-sm font-semibold text-muted-foreground mb-3 px-1">اقلام سفارش</h3>
                    <div className="flex items-center justify-between p-4 rounded-xl border bg-background">
                        <div>
                            <p className="font-medium">{itemTitle}</p>
                            <p className="text-xs text-muted-foreground mt-1">{itemDesc}</p>
                            {Number(invoice.duration_extension_amount) > 0 && (
                              <div className="mt-3 rounded-xl bg-primary/5 p-3 text-sm space-y-1">
                                <p>افزایش مدت به ۶۰ روز؛ بدون افزایش سرمایه گفتگو</p>
                                <p className="text-xs text-muted-foreground">شامل {formatCurrency(invoice.duration_extension_amount!)} هزینه افزایش مدت در مبلغ فاکتور (پیش از مالیات)</p>
                              </div>
                            )}
                        </div>
                        <div className="text-left font-mono font-bold">
                            {formatCurrency(itemAmount)}
                        </div>
                    </div>
                </div>

            </CardContent>

            {/* Ticket Tear Line */}
            <div className="relative flex items-center justify-between px-6">
                <div className="h-4 w-4 rounded-full bg-background -ml-2 border-r border-t border-b border-border/30 shadow-inner" />
                <div className="h-[1px] flex-1 bg-border border-t border-dashed border-muted-foreground/30" />
                <div className="h-4 w-4 rounded-full bg-background -mr-2 border-l border-t border-b border-border/30 shadow-inner" />
            </div>

            {/* Totals Section */}
            <CardFooter className="flex flex-col gap-4 p-8 bg-muted/10">
                <div className="w-full space-y-2">
                    <div className="flex justify-between text-sm">
                        <span className="text-muted-foreground">مبلغ کل</span>
                        <span className="font-mono">{formatCurrency(itemAmount)}</span>
                    </div>
                    
                    {discountAmount > 0 && (
                        <div className="flex justify-between text-sm text-emerald-600 animate-in slide-in-from-right-2">
                            <span>تخفیف اعمال شده</span>
                            <span className="font-mono">- {formatCurrency(invoice.discount_amount!)}</span>
                        </div>
                    )}

                    <div className="flex justify-between text-sm">
                        <span className="text-muted-foreground">مالیات بر ارزش افزوده {taxRate.toLocaleString("fa-IR")}%</span>
                        <span className="font-mono">{formatCurrency(invoice.tax_amount || "0")}</span>
                    </div>
                </div>
                
                <Separator />
                
                <div className="w-full flex justify-between items-center">
                    <span className="text-base font-bold">مبلغ قابل پرداخت</span>
                    <span className="text-2xl font-black text-primary tracking-tight">
                        {formatCurrency(invoice.total_amount)}
                    </span>
                </div>
            </CardFooter>
        </Card>

        {/* --- RIGHT: ACTIONS PANEL --- */}
        <div className="md:col-span-2 flex flex-col gap-4">
            
            {/* 1. Discount Code (Moved Up) */}
            {!isPaid && !isWaiting && (
                <Card className="border-dashed bg-muted/30">
                    <CardContent className="p-5 space-y-3">
                        <span className="text-xs font-semibold flex items-center gap-2 text-muted-foreground">
                            <Tag className="h-4 w-4" /> کد تخفیف
                        </span>
                        <div className="flex gap-2">
                            <Input 
                                placeholder="کد را وارد کنید..." 
                                className="bg-background text-center font-mono placeholder:font-sans"
                                value={discountCode}
                                onChange={(e) => setDiscountCode(e.target.value)}
                                disabled={isApplyingDiscount}
                            />
                            <Button size="icon" variant="secondary" onClick={handleApplyDiscount} disabled={!discountCode || isApplyingDiscount}>
                                {isApplyingDiscount ? <Loader2 className="h-4 w-4 animate-spin"/> : <Check className="h-4 w-4"/>}
                            </Button>
                        </div>
                    </CardContent>
                </Card>
            )}

            {/* 2. Payment Action Card */}
            <Card className="border-none shadow-lg bg-card/80 backdrop-blur">
                <CardContent className="p-5">
                    
                    {/* --- SCENARIO 1: PAID --- */}
                    {isPaid ? (
                        <div className="flex flex-col items-center gap-3 py-4 text-center animate-in fade-in zoom-in-95">
                            <div className="h-14 w-14 rounded-full bg-emerald-100 dark:bg-emerald-900/30 flex items-center justify-center text-emerald-600 mb-2 shadow-sm">
                                <Check className="h-7 w-7" />
                            </div>
                            <div>
                                <h3 className="font-bold text-lg">پرداخت موفق</h3>
                                <p className="text-xs text-muted-foreground mt-1">این سفارش تکمیل و فعال شده است.</p>
                            </div>
                            <Button variant="outline" className="w-full mt-4" onClick={() => router.push('/dashboard')}>
                                بازگشت به داشبورد
                            </Button>
                        </div>
                    ) 
                    /* --- SCENARIO 2: WAITING APPROVAL --- */
                    : isWaiting ? (
                        <div className="flex flex-col items-center gap-3 py-4 text-center animate-in fade-in zoom-in-95">
                            <div className="h-14 w-14 rounded-full bg-blue-100 dark:bg-blue-900/30 mx-auto flex items-center justify-center text-blue-600 mb-2 shadow-sm animate-pulse">
                                <Clock className="h-7 w-7" />
                            </div>
                            <div>
                                <h3 className="font-bold text-blue-700 dark:text-blue-400 text-lg">در حال بررسی</h3>
                                <p className="text-xs text-muted-foreground mt-2 leading-relaxed text-justify px-2">
                                    اطلاعات پرداخت شما ثبت شده است. کارشناسان ما در اسرع وقت آن را بررسی و تایید خواهند کرد.
                                </p>
                            </div>
                            <div className="bg-muted/50 p-3 rounded-lg text-xs font-mono w-full border border-border/50">
                                Ref ID: {invoice.transaction_ref_id}
                            </div>
                            <Button variant="ghost" size="sm" className="w-full text-xs text-muted-foreground hover:text-foreground" onClick={fetchInvoice}>
                                بروزرسانی وضعیت
                            </Button>
                        </div>
                    )
                    /* --- SCENARIO 3: ZERO AMOUNT (FREE ACTIVATION) --- */
                    : payableAmount === 0 ? (
                        <div className="flex flex-col items-center gap-4 py-2 text-center animate-in fade-in zoom-in-95">
                            <div className="space-y-1">
                                <h3 className="font-bold text-lg text-emerald-700 dark:text-emerald-400">سفارش رایگان</h3>
                                <p className="text-xs text-muted-foreground">هزینه این فاکتور صفر است.</p>
                            </div>
                            <Button 
                                size="lg" 
                                className="w-full bg-emerald-600 hover:bg-emerald-700 text-white font-bold shadow-lg shadow-emerald-500/20"
                                onClick={handleOnlinePayment} // Reusing online payment handler for instant activation
                                disabled={paying}
                            >
                                {paying ? <Loader2 className="h-5 w-5 animate-spin" /> : "ادامه و فعال‌سازی سرویس"}
                            </Button>
                        </div>
                    )
                    /* --- SCENARIO 4: PENDING (PAYMENT FORM) --- */
                    : (
                        <Tabs defaultValue={ENABLE_ONLINE_PAYMENT ? "online" : "manual"} className="w-full">
                            {/* Render Tabs List only if Online is enabled, otherwise hide navigation */}
                            {ENABLE_ONLINE_PAYMENT && (
                                <TabsList className="grid w-full grid-cols-2 mb-4">
                                    <TabsTrigger value="online">درگاه آنلاین</TabsTrigger>
                                    <TabsTrigger value="manual">کارت به کارت</TabsTrigger>
                                </TabsList>
                            )}
                            
                            {/* Manual Payment (Default / Forced) */}
                            <TabsContent value="manual" className="mt-0">
                                {!ENABLE_ONLINE_PAYMENT && (
                                    <div className="mb-4 flex items-center gap-2 text-xs font-bold text-muted-foreground border-b border-border/50 pb-2">
                                        <CreditCard className="w-4 h-4" /> روش پرداخت: کارت به کارت
                                    </div>
                                )}
                                <ManualPaymentForm 
                                    invoiceId={invoice.id} 
                                    onSuccess={() => fetchInvoice()} 
                                />
                            </TabsContent>

                            {/* Online Payment (Conditionally Rendered) */}
                            {ENABLE_ONLINE_PAYMENT && (
                                <TabsContent value="online" className="mt-0">
                                    <div className="space-y-4 pt-2 animate-in fade-in slide-in-from-left-2">
                                        <div className="rounded-xl bg-blue-50/50 dark:bg-blue-900/10 p-4 border border-blue-100 dark:border-blue-900/50 flex flex-col items-center gap-2 text-center">
                                            <div className="p-2 bg-blue-100 dark:bg-blue-900/30 rounded-full text-blue-600">
                                                <CreditCard className="w-5 h-5" />
                                            </div>
                                            <p className="text-xs text-muted-foreground leading-relaxed">
                                                انتقال امن به درگاه پرداخت زیبال.
                                                <br/>
                                                <span className="font-bold text-blue-600 dark:text-blue-400">فعال‌سازی آنی پس از پرداخت.</span>
                                            </p>
                                        </div>
                                        <Button 
                                            size="lg" 
                                            className="w-full h-12 shadow-md font-bold"
                                            onClick={handleOnlinePayment}
                                            disabled={paying}
                                        >
                                            {paying ? <Loader2 className="h-5 w-5 animate-spin" /> : "اتصال به درگاه زیبال"}
                                        </Button>
                                    </div>
                                </TabsContent>
                            )}
                        </Tabs>
                    )}

                </CardContent>
            </Card>

        </div>

      </div>
    </div>
  );
}
