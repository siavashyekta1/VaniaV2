"use client";

import { useId, useState } from "react";
import { CalendarDays, Check, ChevronLeft, Loader2, Sparkles, Zap } from "lucide-react";
import { BillingProduct } from "@/lib/types";
import { cn, formatExactCurrency } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

type Props = {
  products: BillingProduct[];
  currentPlanName?: string | null;
  processingId: number | null;
  onPurchase: (product: BillingProduct, extendDuration?: boolean) => void;
};

const number = (value: string | number) => Number(value).toLocaleString("fa-IR");
const money = formatExactCurrency;
const professionNames: Record<string, string> = {
  psychologist: "روانشناسان", psychiatrist: "روانپزشکان",
  lawyer: "وکلا", general_doctor: "پزشکان عمومی",
};

function groupProducts(products: BillingProduct[]) {
  const groups = new Map<string, { title: string; products: BillingProduct[] }>();
  products.forEach((product) => {
    const plan = product.plan_details!;
    const professions = [...(plan.eligible_expert_professions || [])].sort();
    const key = `${plan.audience}:${professions.join(",")}`;
    if (!groups.has(key)) groups.set(key, {
      title: plan.audience === "EXPERT"
        ? `اشتراک تخصصی ${professions.map((p) => professionNames[p] || p).join("، ")}`
        : "اشتراک عمومی",
      products: [],
    });
    groups.get(key)!.products.push(product);
  });
  return [...groups.values()].map((group) => ({ ...group,
    products: group.products.sort((a, b) => Number(a.plan_details!.included_credits) - Number(b.plan_details!.included_credits)),
  }));
}

function Assistants({ product }: { product: BillingProduct }) {
  const agents = product.plan_details?.included_agents || [];
  const item = (name: string) => <li key={name} className="flex items-center gap-2">
      <Check className="size-4 shrink-0 text-teal-600 dark:text-teal-400" /><span>{name}</span>
    </li>;
  return <div className="space-y-3 text-sm text-muted-foreground">
    <ul className="space-y-2">{agents.slice(0, 3).map(item)}</ul>
    {agents.length > 3 && <details>
      <summary className="cursor-pointer text-xs font-medium text-primary">مشاهده {number(agents.length - 3)} دستیار دیگر</summary>
      <ul className="mt-3 space-y-2">{agents.slice(3).map(item)}</ul>
    </details>}
  </div>;
}

function MonthlyCard({ product, currentPlanName, processingId, onPurchase }: Omit<Props, "products"> & { product: BillingProduct }) {
  const [extend, setExtend] = useState(false);
  const checkboxId = useId();
  const plan = product.plan_details!;
  const current = currentPlanName === plan.name;
  const price = Number(product.price) + (extend ? 500_000 : 0);
  return <article className={cn("flex flex-col gap-6 rounded-3xl border bg-card p-6 shadow-sm transition-shadow hover:shadow-md", current && "border-primary ring-1 ring-primary/20")}>
    <div className="space-y-3">
      <div className="flex items-start justify-between gap-2">
        <h3 className="text-base font-bold leading-7">{plan.name}</h3>
        {current && <span className="shrink-0 rounded-full bg-primary/10 px-2 py-1 text-xs text-primary">طرح فعلی</span>}
      </div>
      <p className="flex items-center gap-1.5 text-sm text-muted-foreground"><CalendarDays className="size-4" />{extend ? "۶۰ روز فرصت استفاده" : "۳۰ روز فرصت استفاده"}</p>
    </div>
    <div className="rounded-2xl bg-muted/50 p-4">
      <p className="text-3xl font-black tabular-nums">{number(plan.included_credits)}</p>
      <p className="mt-1 text-sm text-muted-foreground">سرمایه گفتگو برای کل دوره</p>
    </div>
    <Assistants product={product} />
    <label htmlFor={checkboxId} className={cn("mt-auto flex cursor-pointer items-start gap-3 rounded-2xl border p-4 transition-colors", extend ? "border-primary/50 bg-primary/5" : "border-dashed hover:bg-muted/40")}>
      <Checkbox id={checkboxId} checked={extend} onCheckedChange={(checked) => setExtend(checked === true)} disabled={processingId !== null} className="mt-1" />
      <span className="space-y-1 text-sm">
        <span className="block font-semibold">یک ماه فرصت بیشتر</span>
        <span className="block text-muted-foreground">۵۰۰٬۰۰۰ تومان بیشتر؛ سرمایه گفتگو ثابت می‌ماند.</span>
      </span>
    </label>
    <div className="space-y-3">
      <p className="text-2xl font-extrabold tabular-nums">{money(price)}</p>
      <p className="text-xs text-muted-foreground">قیمت کل دوره، پیش از ۱۰٪ مالیات</p>
      <Button size="lg" className="w-full rounded-xl" disabled={processingId !== null} onClick={() => onPurchase(product, extend)}>
        {processingId === product.id ? <Loader2 className="size-4 animate-spin" /> : <>خرید اشتراک<ChevronLeft className="size-4" /></>}
      </Button>
    </div>
  </article>;
}

function SixMonthCard({ products, processingId, onPurchase }: Props) {
  const [index, setIndex] = useState(0);
  const sliderId = useId();
  const selectedIndex = Math.min(index, products.length - 1);
  const product = products[selectedIndex];
  const plan = product.plan_details!;
  return <article className="overflow-hidden rounded-[2rem] border border-teal-500/20 bg-card shadow-lg shadow-teal-900/5">
    <div className="grid lg:grid-cols-[1.4fr_1fr]">
      <div className="space-y-8 bg-gradient-to-bl from-teal-500/10 via-teal-500/5 to-transparent p-6 sm:p-9">
        <div className="space-y-3">
          <span className="inline-flex items-center gap-2 rounded-full bg-teal-600/10 px-3 py-1.5 text-xs font-semibold text-teal-700 dark:text-teal-300"><Sparkles className="size-3.5" />متناسب با حجم کار شما</span>
          <h3 className="text-2xl font-bold">شش ماه همراه شما</h3>
          <p className="text-sm leading-7 text-muted-foreground">سرمایه مورد نیازتان را انتخاب کنید؛ برای مصرف آن ۱۸۰ روز فرصت دارید.</p>
        </div>
        <div className="rounded-3xl border border-teal-500/10 bg-background/80 p-5 sm:p-7">
          <label htmlFor={sliderId} className="block text-sm font-medium">میزان سرمایه گفتگو</label>
          <p className="my-5 text-4xl font-black tabular-nums text-teal-700 dark:text-teal-300" aria-live="polite">{number(plan.included_credits)} <span className="text-sm font-normal text-muted-foreground">سرمایه</span></p>
          <input id={sliderId} type="range" min={0} max={products.length - 1} step={1} value={selectedIndex}
            onChange={(event) => setIndex(Number(event.target.value))} disabled={processingId !== null || products.length < 2}
            aria-valuetext={`${number(plan.included_credits)} سرمایه گفتگو، ${money(product.price)}`}
            className="h-6 w-full cursor-pointer accent-teal-600 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-teal-600 disabled:opacity-50" />
          <div className="mt-3 flex justify-between gap-1">
            {products.map((option, optionIndex) => <button key={option.id} type="button" disabled={processingId !== null}
              aria-pressed={selectedIndex === optionIndex} onClick={() => setIndex(optionIndex)}
              className={cn("rounded-lg px-2 py-2 text-xs sm:text-sm transition-colors focus-visible:outline-2 focus-visible:outline-teal-600", selectedIndex === optionIndex ? "bg-teal-600 text-white font-bold" : "text-muted-foreground hover:bg-teal-500/10")}>
              {number(option.plan_details!.included_credits)}
            </button>)}
          </div>
          <p className="mt-5 text-xs leading-6 text-muted-foreground">نوار را بکشید یا روی مقدار دلخواه بزنید. این سرمایه برای مجموع شش ماه است، نه هر ماه.</p>
        </div>
      </div>
      <div className="flex flex-col gap-6 p-6 sm:p-9 lg:border-r">
        <div className="flex items-center gap-2 text-sm font-semibold"><Zap className="size-4 text-teal-600" />دسترسی به دستیارهای تخصصی</div>
        <Assistants product={product} />
        <div className="mt-auto space-y-4 border-t pt-6">
          <div className="flex items-center justify-between text-sm text-muted-foreground"><span>مدت اعتبار</span><span className="font-semibold text-foreground">۶ ماه · ۱۸۰ روز</span></div>
          <p className="text-2xl sm:text-3xl font-black tabular-nums" aria-live="polite">{money(product.price)}</p>
          <p className="text-xs text-muted-foreground">قیمت کل شش ماه، پیش از ۱۰٪ مالیات</p>
          <Button size="lg" className="w-full rounded-xl bg-teal-700 text-white hover:bg-teal-800" disabled={processingId !== null} onClick={() => onPurchase(product)}>
            {processingId === product.id ? <Loader2 className="size-4 animate-spin" /> : <>خرید {number(plan.included_credits)} سرمایه<ChevronLeft className="size-4" /></>}
          </Button>
          <p className="text-center text-xs leading-6 text-muted-foreground">سرمایه انتخاب‌شده پس از پرداخت، یکجا اضافه می‌شود.</p>
        </div>
      </div>
    </div>
  </article>;
}

export function SubscriptionPlans(props: Props) {
  const monthly = groupProducts(props.products.filter((p) => p.plan_details?.duration_days === 30));
  const sixMonth = groupProducts(props.products.filter((p) => p.plan_details?.duration_days === 180));
  return <Tabs defaultValue="monthly" dir="rtl" className="gap-6">
    <TabsList aria-label="مدت اشتراک" className="h-auto w-full max-w-md self-center rounded-2xl p-1.5">
      <TabsTrigger value="monthly" className="rounded-xl px-4 py-3">اشتراک ۱ ماهه</TabsTrigger>
      <TabsTrigger value="six-month" className="rounded-xl px-4 py-3">اشتراک ۶ ماهه</TabsTrigger>
    </TabsList>
    <TabsContent value="monthly" className="space-y-8">
      {monthly.map((group) => <section key={group.title} className="space-y-4">
        {monthly.length > 1 && <h3 className="font-bold">{group.title}</h3>}
        <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">{group.products.map((product) => <MonthlyCard key={product.id} {...props} product={product} />)}</div>
      </section>)}
      {!monthly.length && <EmptyPlans />}
    </TabsContent>
    <TabsContent value="six-month" className="space-y-8">
      {sixMonth.map((group) => <section key={group.title} className="space-y-4">
        {sixMonth.length > 1 && <h3 className="font-bold">{group.title}</h3>}
        <SixMonthCard {...props} key={group.products.map((p) => p.id).join(",")} products={group.products} />
      </section>)}
      {!sixMonth.length && <EmptyPlans />}
    </TabsContent>
  </Tabs>;
}

function EmptyPlans() {
  return <p className="rounded-3xl border border-dashed p-10 text-center text-sm text-muted-foreground">در حال حاضر اشتراکی با این مدت برای حساب شما ارائه نشده است.</p>;
}
