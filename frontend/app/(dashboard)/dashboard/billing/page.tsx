"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Crown, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Separator } from "@/components/ui/separator";
import { WalletOverview } from "@/components/billing/WalletOverview";
import { SubscriptionPlans } from "@/components/billing/SubscriptionPlans";
import { API_BASE_URL, getAuthHeaders } from "@/lib/api";
import { useUser } from "@/hooks/use-user";
import { BillingProduct } from "@/lib/types";
import { APP_CONFIG } from "@/lib/config";

export default function BillingPage() {
  const { user, loading: userLoading, refreshUser } = useUser();
  const router = useRouter();
  const isAdminUser = Boolean(user?.is_staff || user?.is_superuser);
  const [plans, setPlans] = useState<BillingProduct[]>([]);
  const [topUps, setTopUps] = useState<BillingProduct[]>([]);
  const [loadingProducts, setLoadingProducts] = useState(true);
  const [processingId, setProcessingId] = useState<number | null>(null);

  const fetchProducts = useCallback(async () => {
    setLoadingProducts(true);
    try {
      const path = isAdminUser ? "/api/billing/admin/products/" : "/api/billing/products/";
      const res = await fetch(`${API_BASE_URL}${path}?_=${Date.now()}`, { headers: getAuthHeaders(), cache: "no-store" });
      if (!res.ok) throw new Error("خطا در دریافت لیست محصولات");
      const response = await res.json();
      const products: BillingProduct[] = Array.isArray(response) ? response : response.results || [];
      setPlans(products.filter((p) => p.plan_details));
      setTopUps(products.filter((p) => !p.plan_details));
    } catch {
      toast.error("خطا در دریافت لیست محصولات");
    } finally {
      setLoadingProducts(false);
    }
  }, [isAdminUser]);

  useEffect(() => { refreshUser(); }, [refreshUser]);
  useEffect(() => {
    if (!userLoading) fetchProducts();
  }, [fetchProducts, userLoading, user?.role_slug, user?.expert_profession_slug, user?.is_expert_verified, user?.is_staff, user?.is_superuser]);

  const handlePurchase = async (product: BillingProduct, extendDuration = false) => {
    if (processingId !== null) return;
    setProcessingId(product.id);
    try {
      const res = await fetch(`${API_BASE_URL}/api/billing/purchase/`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...getAuthHeaders() },
        body: JSON.stringify({ id: product.id, extend_duration: extendDuration }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error || data?.detail || "خطا در ایجاد سفارش");
      router.push(data.redirect_url);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "خطا در اتصال به سرور");
    } finally {
      setProcessingId(null);
    }
  };

  if (userLoading) return <div className="flex h-[50vh] items-center justify-center"><Loader2 className="animate-spin text-muted-foreground" /></div>;

  const selectedProfession = user?.expert_profession_slug;
  const matchesProfession = (product: BillingProduct) => {
    const plan = product.plan_details;
    const eligible = plan?.eligible_expert_professions || [];
    return plan?.audience === "EXPERT" && (!eligible.length || eligible.includes(selectedProfession!));
  };
  const visiblePlans = isAdminUser && selectedProfession ? plans.filter(matchesProfession) : plans;
  const otherPlans = isAdminUser && selectedProfession ? plans.filter((p) => !matchesProfession(p)) : [];
  const currentPlanName = user?.wallet?.active_plan_name;
  const activeDescription = plans.find((p) => p.plan_details?.name === currentPlanName)?.plan_details?.description;
  const selectorProps = { currentPlanName, processingId, onPurchase: handlePurchase };

  return <div className="mx-auto flex w-full max-w-6xl flex-col space-y-8 pb-10 pt-6" dir="rtl">
    <header className="space-y-1">
      <h1 className="text-2xl font-bold tracking-tight">{APP_CONFIG.TEXT.BILLING_TITLE}</h1>
      <p className="text-muted-foreground">{APP_CONFIG.TEXT.BILLING_DESC}</p>
    </header>
    <WalletOverview wallet={user?.wallet} loading={userLoading} topUps={topUps}
      onTopUp={handlePurchase} processingId={processingId} planDescription={activeDescription} />
    <Separator />
    <section className="space-y-6" id="subscription-plans">
      <div className="flex items-center gap-3">
        <div className="rounded-xl bg-primary/10 p-2 text-primary"><Crown className="size-5" /></div>
        <div>
          <h2 className="text-lg font-bold">{isAdminUser && selectedProfession ? `طرح‌های مناسب ${user?.expert_profession_label || "حوزه تخصصی شما"}` : APP_CONFIG.TEXT.BUY_PLAN_TITLE}</h2>
          <p className="mt-1 text-sm text-muted-foreground">اول مدت اشتراک، بعد میزان سرمایه گفتگوی خود را انتخاب کنید.</p>
        </div>
      </div>
      {loadingProducts ? <div className="grid gap-5 md:grid-cols-3">{[1, 2, 3].map((i) => <div key={i} className="h-80 animate-pulse rounded-3xl bg-muted/40" />)}</div>
        : <SubscriptionPlans {...selectorProps} products={visiblePlans} />}
      {!loadingProducts && otherPlans.length > 0 && <details className="rounded-3xl border p-5">
        <summary className="cursor-pointer text-sm font-semibold">سایر طرح‌های قابل خرید ادمین</summary>
        <div className="pt-6"><SubscriptionPlans {...selectorProps} products={otherPlans} /></div>
      </details>}
    </section>
  </div>;
}
