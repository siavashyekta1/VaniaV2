"use client";

import { 
  Loader2, Zap, Coins, Crown, Plus
} from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { useConfig } from "@/components/providers/config-provider";
import { WalletInfo, BillingProduct } from "@/lib/types";
import { cn, formatCurrency } from "@/lib/utils";
import { APP_CONFIG } from "@/lib/config";

interface WalletOverviewProps {
  wallet?: WalletInfo;
  loading: boolean;
  topUps?: BillingProduct[];
  onTopUp?: (product: BillingProduct) => void;
  processingId?: number | null;
  planDescription?: string; // [NEW] Added prop
}

export function WalletOverview({ 
  wallet, 
  loading, 
  topUps = [], 
  onTopUp, 
  processingId,
  planDescription 
}: WalletOverviewProps) {
  const { config } = useConfig();

  if (loading) {
    return (
      <div className="h-48 w-full flex items-center justify-center border rounded-3xl bg-muted/5 animate-pulse">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  // --- Logic ---
  const balancePlan = parseFloat(wallet?.balance_plan || "0");
  const balancePaid = parseFloat(wallet?.balance_paid || "0");
  const totalPaidBalance = balancePlan + balancePaid;

  const planName = wallet?.active_plan_name;
  const hasPlan = !!planName && wallet?.is_plan_active !== false;
  return (
    <div className="w-full" dir="rtl">
      
      {/* --- SCENARIO A: SUBSCRIBER --- */}
      {hasPlan ? (
        <Card className="shadow-lg border-0 bg-[#151515] text-white overflow-hidden relative rounded-3xl ring-1 ring-white/5">
            
            {/* Ambient Background Glow */}
            <div className="absolute -top-24 -right-24 w-96 h-96 bg-indigo-500/10 rounded-full blur-[120px] pointer-events-none" />

            <CardContent className="p-8">
                <div className="flex flex-col md:flex-row items-stretch gap-8">
                    
                    {/* RIGHT SIDE (Primary Focus): Plan Details */}
                    <div className="flex-1 w-full flex flex-col justify-center space-y-5 relative z-10">
                        
                        {/* Status Label */}
                        <div className="flex items-center gap-2">
                            <div className="flex items-center gap-1.5 bg-indigo-500/10 border border-indigo-500/20 text-indigo-300 px-3 py-1 rounded-full text-[11px] font-bold shadow-sm">
                                <Crown className="w-3.5 h-3.5 fill-indigo-500/20" />
                                <span>اشتراک فعال شما</span>
                            </div>
                        </div>

                        {/* Title & Desc */}
                        <div>
                            <h2 className="text-3xl md:text-4xl font-black tracking-tight text-white mb-3">
                                {planName}
                            </h2>
                            {/* [UPDATED] Uses dynamic description */}
                            <p className="text-zinc-400 text-sm font-medium leading-relaxed max-w-lg">
                                {planDescription || "دسترسی به ابزارهای هوشمند"}
                            </p>
                        </div>

                        <div className="inline-flex items-center self-start gap-2 bg-white/5 border border-white/5 px-3 py-1.5 rounded-lg text-xs text-zinc-400">
                            <span>{wallet?.plan_expires_at
                              ? `پایان اعتبار: ${new Date(wallet.plan_expires_at).toLocaleDateString("fa-IR")}`
                              : "اعتبار طرح قبلی شما بدون انقضا است."}</span>
                        </div>
                    </div>

                    {/* VERTICAL DIVIDER */}
                    <div className="hidden md:block w-px bg-gradient-to-b from-transparent via-white/10 to-transparent my-2" />

                    {/* LEFT SIDE (Secondary): Balance & Top-up */}
                    {/* [UPDATED] Centered Alignment (items-center, justify-center) */}
                    <div className="flex flex-col items-center justify-center gap-6 min-w-[260px] relative z-10">
                        
                        {/* Balance */}
                        <div className="text-center flex flex-col items-center">
                            <span className="text-[11px] text-zinc-500 font-medium mb-1 uppercase tracking-wide">موجودی حساب</span>
                            <div className="flex items-baseline gap-1.5">
                                <span className="text-3xl font-mono font-bold text-white tracking-tight">
                                    {Math.floor(totalPaidBalance).toLocaleString()}
                                </span>
                                <span className="text-xs text-zinc-500">{config.currency_symbol}</span>
                            </div>
                        </div>

                        {/* Top Ups */}
                        <div className="flex flex-col items-center gap-3 w-full">
                        <div className="flex items-center gap-1.5 text-[10px] text-zinc-600 font-medium">
                                <Zap className="w-3 h-3 text-amber-500/70" />
                                افزایش سریع سرمایه گفتگو
                            </div>
                            <div className="flex flex-wrap justify-center gap-2">
                                {topUps.slice(0, 3).map((pack) => (
                                    <TooltipProvider key={pack.id}>
                                        <Tooltip>
                                            <TooltipTrigger asChild>
                                                <Button
                                                    variant="ghost"
                                                    size="sm"
                                                    className="h-8 rounded-full bg-white/5 hover:bg-white/10 border border-white/5 hover:border-white/20 text-zinc-300 text-xs px-3 transition-all"
                                                    disabled={processingId === pack.id}
                                                    onClick={() => onTopUp && onTopUp(pack)}
                                                >
                                                    {processingId === pack.id ? (
                                                        <Loader2 className="w-3 h-3 animate-spin" />
                                                    ) : (
                                                        <>
                                                            <Plus className="w-3 h-3 mr-1 opacity-50" />
                                                            {parseInt(pack.credit_amount).toLocaleString()}
                                                        </>
                                                    )}
                                                </Button>
                                            </TooltipTrigger>
                                              <TooltipContent className="text-xs">
                                                خرید {parseInt(pack.credit_amount).toLocaleString()} {APP_CONFIG.CREDITS.NAME_SINGULAR}
                                            </TooltipContent>
                                        </Tooltip>
                                    </TooltipProvider>
                                ))}
                            </div>
                        </div>

                    </div>
                </div>
            </CardContent>
        </Card>
      ) : (
        /* --- SCENARIO B: FREE USER --- */
        <Card className="bg-card gap-2 border-border shadow-sm flex flex-col justify-center relative overflow-hidden rounded-3xl">
            <CardContent className="space-y-6 relative z-10 p-2 px-8">
                <div className="flex items-center gap-4">
                    <div className="p-3 bg-blue-500/10 rounded-2xl text-blue-500">
                        <Zap className="w-6 h-6" />
                    </div>
                    <div>
                        <h3 className="text-lg font-bold text-foreground">اشتراک فعال ندارید</h3>
                        <p className="text-sm text-muted-foreground">برای استفاده بیشتر، یکی از طرح‌های زیر را انتخاب کنید.</p>
                    </div>
                </div>
            </CardContent>
        </Card>
      )}

    </div>
  );
}
