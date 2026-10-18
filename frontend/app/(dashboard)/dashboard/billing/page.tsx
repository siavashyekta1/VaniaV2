"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2, Crown, Check, Zap } from "lucide-react";
import { toast } from "sonner";

import { Separator } from "@/components/ui/separator";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

import { WalletOverview } from "@/components/billing/WalletOverview";
import { API_BASE_URL, getAuthHeaders } from "@/lib/api";
import { useUser } from "@/hooks/use-user";
import { useConfig } from "@/components/providers/config-provider";
import { BillingProduct } from "@/lib/types";
import { formatCurrency, cn } from "@/lib/utils";
import { APP_CONFIG } from "@/lib/config";

export default function BillingPage() {
  const { user, loading: userLoading, refreshUser } = useUser();
  const { config } = useConfig();
  const router = useRouter();
  const isAdminUser = Boolean(user?.is_staff || user?.is_superuser);
  
  const [plans, setPlans] = useState<BillingProduct[]>([]);
  const [topUps, setTopUps] = useState<BillingProduct[]>([]);
  const [loadingProducts, setLoadingProducts] = useState(true);
  const [processingId, setProcessingId] = useState<number | null>(null);
  
  const fetchProducts = useCallback(async () => {
      const headers = getAuthHeaders();
      setLoadingProducts(true);
      try {
        const cacheBust = Date.now();
        const productsPath = isAdminUser ? "/api/billing/admin/products/" : "/api/billing/products/";
        const res = await fetch(`${API_BASE_URL}${productsPath}?_=${cacheBust}`, {
          headers,
          cache: "no-store",
        });
        if (res.ok) {
            const responseData = await res.json();
            const data: BillingProduct[] = Array.isArray(responseData) 
                ? responseData 
                : (responseData.results || []);

            const planProducts = data.filter((p: any) => p.plan_details);
            const creditProducts = data.filter((p: any) => !p.plan_details);
            
            setPlans(planProducts);
            setTopUps(creditProducts);
        } else {
          toast.error("خطا در دریافت لیست محصولات");
        }
      } catch (err) {
        console.error(err);
        toast.error("خطا در دریافت لیست محصولات");
      } finally {
        setLoadingProducts(false);
      }
  }, [isAdminUser]);

  useEffect(() => {
    refreshUser();
  }, [refreshUser]);

  useEffect(() => {
    if (userLoading) return;
    fetchProducts();
  }, [
    fetchProducts,
    userLoading,
    user?.role_slug,
    user?.expert_profession_slug,
    user?.is_expert_verified,
    user?.is_staff,
    user?.is_superuser,
  ]);

  const handlePurchase = async (product: BillingProduct) => {
    setProcessingId(product.id);
    const headers = getAuthHeaders();

    try {
      const res = await fetch(`${API_BASE_URL}/api/billing/purchase/`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...headers },
        body: JSON.stringify({ id: product.id }),
      });

      if (!res.ok) {
        const payload = await res.json().catch(() => null);
        const message = payload?.error || payload?.detail || "خطا در ایجاد سفارش";
        throw new Error(message);
      }

      const data = await res.json();
      router.push(data.redirect_url); 

    } catch (e: any) {
      toast.error(e.message || "خطا در اتصال به سرور");
    } finally {
      setProcessingId(null);
    }
  };

  if (userLoading) return <div className="flex h-[50vh] items-center justify-center"><Loader2 className="animate-spin text-muted-foreground" /></div>;

  // --- Logic to get Active Plan Description ---
  const currentPlanName = user?.wallet?.active_plan_name;
  const selectedProfessionSlug = user?.expert_profession_slug || null;
  const selectedProfessionLabel = user?.expert_profession_label || "حوزه تخصصی شما";
  const planMatchesSelectedProfession = (prod: BillingProduct) => {
    const plan = prod.plan_details;
    if (!plan || plan.audience !== "EXPERT" || !selectedProfessionSlug) return false;
    const eligible = plan.eligible_expert_professions || [];
    return eligible.length === 0 || eligible.includes(selectedProfessionSlug);
  };
  const professionPlans =
    isAdminUser && selectedProfessionSlug ? plans.filter(planMatchesSelectedProfession) : plans;
  const adminOtherPlans =
    isAdminUser && selectedProfessionSlug ? plans.filter((prod) => !planMatchesSelectedProfession(prod)) : [];
  const visiblePlans = professionPlans;
  
  // Find the plan product that matches the user's active plan name to get the description
  const activePlanProduct = plans.find(
    (p) => p.plan_details?.name === currentPlanName
  );
  const activePlanDescription = activePlanProduct?.plan_details?.description;

  return (
    <div className="flex flex-col w-full space-y-8 pb-10 max-w-6xl mx-auto pt-6" dir="rtl">
      
      {/* Header */}
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-bold tracking-tight">{APP_CONFIG.TEXT.BILLING_TITLE}</h1>
        <p className="text-muted-foreground">{APP_CONFIG.TEXT.BILLING_DESC}</p>
      </div>

      {/* --- SECTION: WALLET OVERVIEW --- */}
      <div className="space-y-4">
          <WalletOverview 
            wallet={user?.wallet} 
            loading={userLoading}
            topUps={topUps}
            onTopUp={handlePurchase}
            processingId={processingId}
            // [NEW] Pass description
            planDescription={activePlanDescription}
          />
      </div>

      <Separator />

      {/* --- SECTION: SUBSCRIPTION PLANS --- */}
      <div className="space-y-6" id="subscription-plans">
         <div className="flex items-center gap-2">
            <div className="p-1.5 bg-indigo-100 dark:bg-indigo-900/30 text-indigo-600 rounded-lg">
                <Crown className="w-5 h-5 fill-current" />
            </div>
            <div>
                <h2 className="text-lg font-bold">
                  {isAdminUser && selectedProfessionSlug ? `طرح‌های مناسب ${selectedProfessionLabel}` : APP_CONFIG.TEXT.BUY_PLAN_TITLE}
                </h2>
                <p className="text-xs text-muted-foreground">
                  {isAdminUser && selectedProfessionSlug
                    ? "براساس حوزه تخصصی انتخاب‌شده در تنظیمات ادمین"
                    : "دسترسی به دستیارهای هوشمند و امکانات ویژه"}
                </p>
            </div>
         </div>

         {loadingProducts ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-6">
                {[1, 2, 3].map(i => <div key={i} className="h-64 bg-muted/20 animate-pulse rounded-xl" />)}
            </div>
         ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-6">
                {visiblePlans.map((prod: any) => {
                    const plan = prod.plan_details; 
                    const isCurrent = currentPlanName === plan.name;

                    return (
                        <Card 
                            key={prod.id} 
                            className={cn(
                                "relative flex flex-col h-full overflow-visible transition-all duration-300 rounded-3xl",
                                isCurrent 
                                    ? "border-2 border-indigo-500 shadow-xl shadow-indigo-500/10 dark:shadow-indigo-900/20 z-10 scale-[1.01]" 
                                    : "border-border hover:border-indigo-200 dark:hover:border-indigo-800 hover:shadow-lg"
                            )}
                        >
                            {/* Active Plan Badge */}
                            {isCurrent && (
                                <div className="absolute -top-3 right-0 left-0 flex justify-center z-20">
                                    <Badge className="bg-indigo-600 hover:bg-indigo-600 text-white border-4 border-background px-4 py-1 text-xs shadow-sm">
                                        طرح فعال فعلی
                                    </Badge>
                                </div>
                            )}

                            {/* Background decoration for active plan */}
                            {isCurrent && (
                                <div className="absolute inset-0 bg-gradient-to-b from-indigo-50/50 to-transparent dark:from-indigo-950/10 pointer-events-none rounded-xl" />
                            )}
                            
                            <CardHeader className={cn("pb-4 relative", isCurrent && "pt-8")}>
                                <div className="flex justify-between items-start">
                                    <h3 className={cn("text-xl font-bold", isCurrent ? "text-indigo-700 dark:text-indigo-400" : "")}>
                                        {plan.name}
                                    </h3>
                                    {isCurrent && <Check className="w-5 h-5 text-indigo-500" />}
                                </div>
                                
                                <div className="flex items-baseline gap-1 mt-3">
                                    <span className="text-3xl font-black tracking-tight">{formatCurrency(prod.price).replace(APP_CONFIG.ECONOMY.CURRENCY_SYMBOL, '')}</span>
                                    <span className="text-sm text-muted-foreground font-medium">{APP_CONFIG.ECONOMY.CURRENCY_SYMBOL}</span>
                                </div>
                                <p className="text-sm text-muted-foreground mt-3 min-h-[40px] leading-relaxed">
                                    {plan.description}
                                </p>
                            </CardHeader>
                            
                            <CardContent className="flex-1 space-y-5 relative">
                                <p className="text-sm font-semibold">
                                    {Number(plan.included_credits).toLocaleString("fa-IR")} سرمایه گفتگو برای کل دوره
                                    {" · "}{plan.duration_days === 180 ? "شش‌ماهه" : "یک‌ماهه"}
                                </p>
                                <Separator className={isCurrent ? "bg-indigo-200 dark:bg-indigo-800/50" : ""} />

                                <div className="space-y-3">
                                    <span className="text-xs font-bold text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
                                        <Crown className="w-3.5 h-3.5" />
                                        دستیارهای شامل شده:
                                    </span>
                                    <ul className="space-y-2">
                                        {plan.included_agents && plan.included_agents.length > 0 ? (
                                            plan.included_agents.map((agentName: string) => (
                                                <li key={agentName} className="flex items-center gap-2.5 text-sm">
                                                    <div className={cn(
                                                        "p-0.5 rounded-full flex items-center justify-center w-4 h-4",
                                                        isCurrent ? "bg-indigo-100 text-indigo-600 dark:bg-indigo-900 dark:text-indigo-400" : "bg-emerald-100 text-emerald-600 dark:bg-emerald-900/50"
                                                    )}>
                                                        <Check className="w-2.5 h-2.5" />
                                                    </div>
                                                    <span className={isCurrent ? "font-medium" : ""}>{agentName}</span>
                                                </li>
                                            ))
                                        ) : (
                                            <li className="text-sm text-muted-foreground italic pl-6 opacity-70">بدون دستیار اختصاصی</li>
                                        )}
                                    </ul>
                                </div>
                            </CardContent>

                            <CardFooter className="pt-2 flex flex-col gap-3 relative">
                                <Button 
                                    className={cn("w-full transition-all rounded-xl", isCurrent && "bg-indigo-600 hover:bg-indigo-700 dark:bg-indigo-600 dark:hover:bg-indigo-500 text-white shadow-md shadow-indigo-500/20")}
                                    variant={isCurrent ? "default" : "default"}
                                    size="lg"
                                    disabled={processingId === prod.id}
                                    onClick={() => handlePurchase(prod)}
                                >
                                    {processingId === prod.id ? (
                                        <Loader2 className="w-5 h-5 animate-spin" />
                                    ) : isCurrent ? (
                                        "افزایش اعتبار طرح"
                                    ) : (
                                        "خرید و فعال‌سازی"
                                    )}
                                </Button>
                                {/* Moved Gift Credit to below the button */}
                                {parseInt(plan.included_credits) > 0 && (
                                    <div className="flex items-center justify-center gap-1.5 text-xs text-muted-foreground opacity-80 mt-1">
                                        <Zap className="w-3 h-3 text-amber-500 fill-amber-500" />
                                        <span>شامل</span>
                                        <span className="font-mono font-bold text-foreground">{parseInt(plan.included_credits).toLocaleString()}</span>
                                        <span>{config.currency_name}</span>
                                    </div>
                                )}
                            </CardFooter>
                        </Card>
                    );
                })}
            </div>
         )}
         {!loadingProducts && visiblePlans.length === 0 && (
            <div className="rounded-xl border border-dashed border-muted-foreground/30 p-6 text-center text-sm text-muted-foreground">
              در حال حاضر طرح قابل نمایش برای حساب شما وجود ندارد.
            </div>
         )}
         {!loadingProducts && adminOtherPlans.length > 0 && (
            <div className="space-y-4 pt-4">
              <Separator />
              <div>
                <h3 className="text-base font-bold">سایر طرح‌های قابل خرید ادمین</h3>
                <p className="text-xs text-muted-foreground">
                  این طرح‌ها همچنان برای حساب ادمین قابل خرید هستند، اما مربوط به حوزه تخصصی انتخاب‌شده نیستند.
                </p>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-6">
                {adminOtherPlans.map((prod: any) => {
                    const plan = prod.plan_details;
                    const isCurrent = currentPlanName === plan.name;

                    return (
                        <Card
                            key={prod.id}
                            className={cn(
                                "relative flex flex-col h-full overflow-visible transition-all duration-300 rounded-3xl opacity-90",
                                isCurrent
                                    ? "border-2 border-indigo-500 shadow-xl shadow-indigo-500/10 dark:shadow-indigo-900/20 z-10 scale-[1.01]"
                                    : "border-border hover:border-indigo-200 dark:hover:border-indigo-800 hover:shadow-lg"
                            )}
                        >
                            {isCurrent && (
                                <div className="absolute -top-3 right-0 left-0 flex justify-center z-20">
                                    <Badge className="bg-indigo-600 hover:bg-indigo-600 text-white border-4 border-background px-4 py-1 text-xs shadow-sm">
                                        طرح فعال فعلی
                                    </Badge>
                                </div>
                            )}

                            <CardHeader className={cn("pb-4 relative", isCurrent && "pt-8")}>
                                <div className="flex justify-between items-start">
                                    <h3 className={cn("text-xl font-bold", isCurrent ? "text-indigo-700 dark:text-indigo-400" : "")}>
                                        {plan.name}
                                    </h3>
                                    {isCurrent && <Check className="w-5 h-5 text-indigo-500" />}
                                </div>
                                <div className="flex items-baseline gap-1 mt-3">
                                    <span className="text-3xl font-black tracking-tight">{formatCurrency(prod.price).replace(APP_CONFIG.ECONOMY.CURRENCY_SYMBOL, '')}</span>
                                    <span className="text-sm text-muted-foreground font-medium">{APP_CONFIG.ECONOMY.CURRENCY_SYMBOL}</span>
                                </div>
                                <p className="text-sm text-muted-foreground mt-3 min-h-[40px] leading-relaxed">
                                    {plan.description}
                                </p>
                            </CardHeader>

                            <CardContent className="flex-1 space-y-5 relative">
                                <Separator className={isCurrent ? "bg-indigo-200 dark:bg-indigo-800/50" : ""} />
                                <div className="space-y-3">
                                    <span className="text-xs font-bold text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
                                        <Crown className="w-3.5 h-3.5" />
                                        دستیارهای شامل شده:
                                    </span>
                                    <ul className="space-y-2">
                                        {plan.included_agents && plan.included_agents.length > 0 ? (
                                            plan.included_agents.map((agentName: string) => (
                                                <li key={agentName} className="flex items-center gap-2.5 text-sm">
                                                    <div className={cn(
                                                        "p-0.5 rounded-full flex items-center justify-center w-4 h-4",
                                                        isCurrent ? "bg-indigo-100 text-indigo-600 dark:bg-indigo-900 dark:text-indigo-400" : "bg-emerald-100 text-emerald-600 dark:bg-emerald-900/50"
                                                    )}>
                                                        <Check className="w-2.5 h-2.5" />
                                                    </div>
                                                    <span className={isCurrent ? "font-medium" : ""}>{agentName}</span>
                                                </li>
                                            ))
                                        ) : (
                                            <li className="text-sm text-muted-foreground italic pl-6 opacity-70">بدون دستیار اختصاصی</li>
                                        )}
                                    </ul>
                                </div>
                            </CardContent>

                            <CardFooter className="pt-2 flex flex-col gap-3 relative">
                                <Button
                                    className={cn("w-full transition-all rounded-xl", isCurrent && "bg-indigo-600 hover:bg-indigo-700 dark:bg-indigo-600 dark:hover:bg-indigo-500 text-white shadow-md shadow-indigo-500/20")}
                                    variant={isCurrent ? "default" : "default"}
                                    size="lg"
                                    disabled={processingId === prod.id}
                                    onClick={() => handlePurchase(prod)}
                                >
                                    {processingId === prod.id ? (
                                        <Loader2 className="w-5 h-5 animate-spin" />
                                    ) : isCurrent ? (
                                        "افزایش اعتبار طرح"
                                    ) : (
                                        "خرید و فعال‌سازی"
                                    )}
                                </Button>
                                {parseInt(plan.included_credits) > 0 && (
                                    <div className="flex items-center justify-center gap-1.5 text-xs text-muted-foreground opacity-80 mt-1">
                                        <Zap className="w-3 h-3 text-amber-500 fill-amber-500" />
                                        <span>شامل</span>
                                        <span className="font-mono font-bold text-foreground">{parseInt(plan.included_credits).toLocaleString()}</span>
                                        <span>{config.currency_name}</span>
                                    </div>
                                )}
                            </CardFooter>
                        </Card>
                    );
                })}
              </div>
            </div>
         )}
      </div>
    </div>
  );
}
