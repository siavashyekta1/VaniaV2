"use client";

import type { ComponentProps, ReactNode } from "react";
import { BookOpen, CheckCircle2, Compass, MessageSquareText, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import type { UserData } from "@/lib/types";
import { cn } from "@/lib/utils";
import { hasExpertFeatures, isVisitorRoleSlug, normalizeRoleSlug } from "@/lib/roles";

type GuideAudience = "all" | "visitor" | "expert";

type GuideCard = {
  title: string;
  description: string;
  items?: string[];
  audience?: GuideAudience[];
  professions?: string[];
};

type GuideStep = {
  title: string;
  description: string;
  audience?: GuideAudience[];
};

const commonSteps: GuideStep[] = [
  {
    title: "از پیشخوان شروع کنید",
    description: "در پیشخوان وضعیت حساب، دستیارهای قابل استفاده، پیام‌ها و مسیرهای مهم را یکجا می‌بینید.",
  },
  {
    title: "دستیار مناسب را انتخاب کنید",
    description: "هر دستیار برای یک نیاز مشخص ساخته شده است. قبل از شروع، توضیح کارت دستیار و محدودیت‌های دسترسی را بخوانید.",
  },
  {
    title: "مسئله را با زمینه کافی توضیح دهید",
    description: "هرچه هدف، شرایط، فایل‌ها و محدودیت‌های خود را دقیق‌تر بگویید، پاسخ دستیار کاربردی‌تر می‌شود.",
  },
  {
    title: "خروجی‌ها را در مسیر من و پیام‌ها پیگیری کنید",
    description: "تحلیل‌ها، برنامه‌ها، پیام‌ها و ادامه گفتگوها از بخش‌های مربوطه قابل پیگیری هستند.",
  },
];

const roleSteps: GuideStep[] = [
  {
    title: "برای مراجعه به متخصص آماده شوید",
    description: "اگر مراجع هستید، ابتدا مشکل، علائم، هدف جلسه و مدارک مرتبط را ثبت کنید تا متخصص تصویر دقیق‌تری داشته باشد.",
    audience: ["visitor"],
  },
  {
    title: "متخصص مناسب را پیدا کنید",
    description: "از بخش متخصصان من می‌توانید ارتباط‌ها، درخواست‌ها و مسیرهای مرتبط با متخصصان را دنبال کنید.",
    audience: ["visitor"],
  },
  {
    title: "قبل از کار، مراجع یا پرونده را انتخاب کنید",
    description: "اگر متخصص هستید، دستیارهای تخصصی وقتی بهترین خروجی را می‌دهند که مراجع و زمینه پرونده درست انتخاب شده باشد.",
    audience: ["expert"],
  },
  {
    title: "خروجی دستیار را به عنوان پیش‌نویس حرفه‌ای بررسی کنید",
    description: "تحلیل‌ها، خلاصه‌ها و برنامه‌ها باید با قضاوت تخصصی شما بازبینی و تکمیل شوند.",
    audience: ["expert"],
  },
];

const assistantCards: GuideCard[] = [
  {
    title: "روانیار",
    description: "برای گفتگو درباره احساسات، روابط، نگرانی‌ها و تصمیم‌های شخصی.",
    items: ["مدیریت استرس و اضطراب", "شفاف‌سازی احساسات", "آماده شدن برای مراجعه به روانشناس"],
    audience: ["visitor"],
  },
  {
    title: "همیار مراجع",
    description: "برای آماده‌سازی اطلاعات قبل از مراجعه به متخصص.",
    items: ["شرح مسئله", "جمع‌آوری سوابق", "پیشنهاد مسیر مراجعه"],
    audience: ["visitor"],
  },
  {
    title: "همیار تحصیلی و مطالعه",
    description: "برای برنامه‌ریزی درس، تمرکز، عادت مطالعه و آمادگی امتحان.",
    items: ["برنامه روزانه", "کاهش اهمال‌کاری", "پیگیری پیشرفت"],
    audience: ["visitor"],
  },
  {
    title: "همیار شغلی",
    description: "برای تصمیم‌های شغلی، رزومه، تعارض کاری و تغییر مسیر حرفه‌ای.",
    items: ["انتخاب مسیر", "آمادگی مصاحبه", "مدیریت چالش‌های محیط کار"],
    audience: ["visitor"],
  },
  {
    title: "همیار عدالت",
    description: "برای آگاهی اولیه و آماده‌سازی اطلاعات حقوقی؛ جایگزین مشاوره وکالت نیست.",
    items: ["مرور ساده قرارداد", "جمع‌آوری اطلاعات پرونده", "آماده‌سازی سوال برای وکیل"],
    audience: ["visitor"],
  },
  {
    title: "دستیار تخصصی پرونده",
    description: "برای متخصصان؛ با زمینه مراجع کار می‌کند و کنار چت، داده‌های ساختاریافته را در بوم نشان می‌دهد.",
    items: ["خلاصه پرونده", "سوال‌های پیگیری", "پیشنهاد گام بعدی"],
    audience: ["expert"],
  },
];

const platformCards: GuideCard[] = [
  {
    title: "گفتگو",
    description: "محل اصلی پرسیدن سوال، ادامه دادن بحث و دریافت خروجی از دستیارها.",
  },
  {
    title: "بوم همکاری",
    description: "در دستیارهای دارای بوم، اطلاعات مهم کنار گفتگو به‌صورت ساختاریافته نمایش داده یا تکمیل می‌شود.",
  },
  {
    title: "بارگذاری مدارک",
    description: "فایل‌ها، تصاویر و اسناد مرتبط را اضافه کنید تا دستیار زمینه دقیق‌تری داشته باشد.",
  },
  {
    title: "مسیر من",
    description: "برای مراجع، محل پیگیری تحلیل‌ها، توصیه‌ها و برنامه‌های ادامه مسیر است.",
    audience: ["visitor"],
  },
  {
    title: "متخصصان من",
    description: "برای مراجع، محل مشاهده ارتباط با متخصصان و پیگیری درخواست‌هاست.",
    audience: ["visitor"],
  },
  {
    title: "مدیریت مراجعین",
    description: "برای متخصص، محل مشاهده مراجعین، انتخاب زمینه همکاری و پیگیری پرونده‌هاست.",
    audience: ["expert"],
  },
  {
    title: "پیام‌ها",
    description: "برای پیگیری ارتباط‌ها و پیام‌های مهم مربوط به همکاری‌ها.",
  },
  {
    title: "طرح‌ها و سفارشات",
    description: "برای مدیریت اشتراک، اعتبار گفتگو، فاکتورها و پرداخت‌ها.",
  },
  {
    title: "تنظیمات",
    description: "برای تکمیل پروفایل، اطلاعات حساب و موارد مرتبط با نقش کاربری.",
  },
];

const expertProfessionCards: GuideCard[] = [
  {
    title: "راهنمای روانشناس",
    description: "از وانیا برای منظم‌سازی داده‌های مراجع، آماده‌سازی جلسه و ساخت پیش‌نویس برنامه مداخله استفاده کنید.",
    items: ["مصاحبه اولیه و خلاصه‌سازی", "تحلیل تست‌ها و نشانه‌ها", "طرح درمان، تکلیف و پیگیری جلسه"],
    audience: ["expert"],
    professions: ["psychologist"],
  },
  {
    title: "راهنمای روانپزشک",
    description: "از وانیا برای مرور سوابق، نشانه‌ها و آماده‌سازی ساختار تصمیم‌گیری بالینی استفاده کنید.",
    items: ["خلاصه سوابق مراجع", "مرور علائم و روند درمان", "آماده‌سازی نکات پیگیری برای ویزیت"],
    audience: ["expert"],
    professions: ["psychiatrist"],
  },
  {
    title: "راهنمای پزشک",
    description: "از وانیا برای مرتب‌سازی شرح حال، مدارک پزشکی و سوال‌های تکمیلی استفاده کنید.",
    items: ["مرور مدارک و آزمایش‌ها", "خلاصه علائم", "پیشنهاد سوال‌های تکمیلی برای ارزیابی"],
    audience: ["expert"],
    professions: ["general_doctor", "doctor", "physician"],
  },
  {
    title: "راهنمای وکیل",
    description: "از وانیا برای نظم‌دهی اطلاعات پرونده، مرور اسناد و آماده‌سازی پیش‌نویس‌های قابل بازبینی استفاده کنید.",
    items: ["استخراج نکات کلیدی سند", "ساخت خط زمانی پرونده", "آماده‌سازی سوال‌ها و متن اولیه"],
    audience: ["expert"],
    professions: ["lawyer"],
  },
];

const expertFallbackCards: GuideCard[] = [
  {
    title: "راهنمای عمومی متخصصان",
    description: "اگر تخصص شما در راهنمای اختصاصی نیامده، از وانیا برای جمع‌آوری زمینه، خلاصه‌سازی و ساخت پیش‌نویس قابل بازبینی استفاده کنید.",
    items: ["انتخاب مراجع قبل از شروع", "ثبت داده‌های مهم در گفتگو یا بوم", "بازبینی حرفه‌ای خروجی قبل از استفاده"],
    audience: ["expert"],
  },
];

const safetyCards: GuideCard[] = [
  {
    title: "حدود استفاده",
    description: "وانیا دستیار تصمیم‌یار است و جایگزین متخصص، تشخیص قطعی، درمان، نسخه، رای حقوقی یا اقدام اورژانسی نیست.",
  },
  {
    title: "اطلاعات دقیق‌تر، پاسخ بهتر",
    description: "نام بخش، هدف، زمان‌بندی، محدودیت‌ها و فایل‌های مرتبط را بنویسید. از ارسال اطلاعات غیرضروری و حساس خودداری کنید.",
  },
  {
    title: "ادامه مسیر",
    description: "بعد از هر خروجی، از دستیار بخواهید خلاصه، برنامه قدم‌به‌قدم یا سوال‌های لازم برای مراجعه بعدی را آماده کند.",
  },
];

type GuideModalProps = {
  user?: UserData | null;
  triggerLabel?: string;
  triggerMode?: "button" | "text" | "custom";
  buttonVariant?: ComponentProps<typeof Button>["variant"];
  triggerClassName?: string;
  trigger?: ReactNode;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
};

function getAudience(user?: UserData | null): GuideAudience {
  if (hasExpertFeatures(user)) return "expert";
  const role = normalizeRoleSlug(user?.role_slug);
  if (isVisitorRoleSlug(role)) return "visitor";
  return "visitor";
}

function isVisibleForAudience(card: GuideCard | GuideStep, audience: GuideAudience) {
  return !card.audience || card.audience.includes("all") || card.audience.includes(audience);
}

function isVisibleForProfession(card: GuideCard, professionSlug?: string | null) {
  if (!card.professions?.length) return true;
  return Boolean(professionSlug && card.professions.includes(professionSlug));
}

function GuideCardList({ cards }: { cards: GuideCard[] }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {cards.map((card) => (
        <article key={card.title} className="rounded-lg border border-border/50 bg-background/70 p-3">
          <h4 className="font-semibold">{card.title}</h4>
          <p className="mt-1 text-xs leading-6 text-muted-foreground">{card.description}</p>
          {!!card.items?.length && (
            <ul className="mt-2 space-y-1 text-xs text-foreground/85">
              {card.items.map((item) => (
                <li key={item} className="flex items-start gap-2">
                  <CheckCircle2 className="mt-1 size-3 shrink-0 text-primary" />
                  <span>{item}</span>
                </li>
              ))}
            </ul>
          )}
        </article>
      ))}
    </div>
  );
}

export function GuideModal({
  user,
  triggerLabel = "مشاهده راهنمای کامل",
  triggerMode = "button",
  buttonVariant = "outline",
  triggerClassName,
  trigger,
  open,
  onOpenChange,
}: GuideModalProps) {
  const audience = getAudience(user);
  const professionSlug = user?.expert_profession_slug || null;
  const isExpert = audience === "expert";
  const roleLabel = isExpert ? user?.expert_profession_label || "متخصص" : "مراجع";
  const steps = [...commonSteps, ...roleSteps].filter((step) => isVisibleForAudience(step, audience));
  const visibleAssistants = assistantCards.filter((card) => isVisibleForAudience(card, audience));
  const visiblePlatformCards = platformCards.filter((card) => isVisibleForAudience(card, audience));
  const visibleExpertCards = expertProfessionCards.filter(
    (card) => isVisibleForAudience(card, audience) && isVisibleForProfession(card, professionSlug),
  );
  const expertCards = visibleExpertCards.length > 0 ? visibleExpertCards : expertFallbackCards;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogTrigger asChild>
        {triggerMode === "custom" && trigger ? (
          trigger
        ) : triggerMode === "text" ? (
          <button
            type="button"
            className={cn("hover:text-zinc-300 transition-colors", triggerClassName)}
          >
            {triggerLabel}
          </button>
        ) : (
          <Button variant={buttonVariant} className={triggerClassName}>
            {triggerLabel}
          </Button>
        )}
      </DialogTrigger>

      <DialogContent className="w-[calc(100vw-2rem)] max-w-4xl max-h-[85vh] overflow-y-auto" dir="rtl">
        <DialogHeader className="text-right">
          <DialogTitle className="flex items-center gap-2 text-xl">
            <BookOpen className="size-5 text-primary" />
            راهنمای وانیا آپ برای {roleLabel}
          </DialogTitle>
          <DialogDescription>
            آموزش کوتاه و نقش‌محور برای اینکه بدانید از کجا شروع کنید، هر بخش چه کاری انجام می‌دهد و چطور خروجی بهتری بگیرید.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-6 text-sm leading-7 text-foreground/90">
          <section className="rounded-lg border border-border/60 bg-muted/20 p-4">
            <div className="flex items-start gap-3">
              <div className="mt-1 rounded-lg bg-primary/10 p-2 text-primary">
                <Sparkles className="size-4" />
              </div>
              <div>
                <h3 className="font-semibold text-base">وانیا آپ چه کمکی می‌کند؟</h3>
                <p className="mt-1">
                  وانیا آپ یک پلتفرم همکاری با دستیارهای هوشمند است. شما می‌توانید گفتگو کنید، مدارک را اضافه کنید،
                  خروجی‌های ساختاریافته را در بوم ببینید و در صورت نیاز مسیر ارتباط با متخصص یا مراجع را پیگیری کنید.
                </p>
                <p className="mt-2 font-medium text-primary">هدف اصلی: تبدیل مسئله پراکنده به قدم بعدی روشن و قابل پیگیری.</p>
              </div>
            </div>
          </section>

          <section>
            <h3 className="mb-3 flex items-center gap-2 font-semibold text-base">
              <Compass className="size-4 text-primary" />
              مسیر شروع سریع
            </h3>
            <div className="grid gap-3">
              {steps.map((step, index) => (
                <div key={step.title} className="flex gap-3 rounded-lg border border-border/50 bg-background/70 p-3">
                  <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-bold text-primary">
                    {(index + 1).toLocaleString("fa-IR")}
                  </span>
                  <div>
                    <p className="font-medium">{step.title}</p>
                    <p className="mt-1 text-xs leading-6 text-muted-foreground">{step.description}</p>
                  </div>
                </div>
              ))}
            </div>
          </section>

          <section>
            <h3 className="mb-3 flex items-center gap-2 font-semibold text-base">
              <MessageSquareText className="size-4 text-primary" />
              دستیارها و کاربردها
            </h3>
            <GuideCardList cards={visibleAssistants} />
          </section>

          <section>
            <h3 className="mb-3 font-semibold text-base">بخش‌های مهم پلتفرم</h3>
            <GuideCardList cards={visiblePlatformCards} />
          </section>

          {isExpert && (
            <section>
              <h3 className="mb-3 font-semibold text-base">راهنمای اختصاصی نقش تخصصی شما</h3>
              <GuideCardList cards={expertCards} />
            </section>
          )}

          <section className="rounded-lg border border-primary/30 bg-primary/5 p-4">
            <h3 className="mb-3 font-semibold text-base">نکات مهم قبل از استفاده</h3>
            <GuideCardList cards={safetyCards} />
          </section>
        </div>
      </DialogContent>
    </Dialog>
  );
}
