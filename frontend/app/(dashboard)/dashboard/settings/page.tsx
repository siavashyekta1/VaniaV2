"use client"

import { useState, useEffect } from "react"
import { 
  User, 
  Lock, 
  Save, 
  Loader2, 
  ShieldCheck, 
  AlertCircle,
  CheckCircle2,
  Stethoscope,
  ChevronDown,
  BadgeCheck,
  UserCog, // New icon for profile settings
  ChevronLeft,
  Clock3,
} from "lucide-react"
import { motion, AnimatePresence } from "framer-motion"

import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Separator } from "@/components/ui/separator"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { useUser } from "@/hooks/use-user"
import {
  API_BASE_URL,
  getAuthHeaders,
  getExpertProfessions,
  setAdminExpertProfession,
  upgradeExpert,
} from "@/lib/api"
import { cn } from "@/lib/utils"
import { toast } from "sonner"
import { hasExpertFeatures, hasVisitorFeatures, isStaffOrAdminUser } from "@/lib/roles"

// [NEW] Import the Doctor Profile Modal
import { DoctorProfileModal } from "@/components/settings/DoctorProfileModal"
import { VisitorBaseProfileModal } from "@/components/settings/VisitorBaseProfileModal"

export default function SettingsPage() {
  const { user, refreshUser } = useUser()

  return (
    <div className="flex flex-col w-full h-full space-y-8 pb-10 max-w-6xl mx-auto pt-6" dir="rtl">
      
      {/* Header */}
      <div className="flex flex-col gap-1 text-start">
        <h1 className="text-2xl font-bold tracking-tight">تنظیمات</h1>
        <p className="text-muted-foreground">
          مدیریت حساب کاربری، امنیت و پروفایل.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        
        <div className="lg:col-span-3 space-y-8">
          
          {/* [NEW] Public Profile Section (Only visible to Doctors) */}
          <section id="doctor-profile">
            <DoctorPublicProfileSection user={user} />
          </section>

          <section id="visitor-profile">
            <VisitorBaseProfileSection user={user} refreshUser={refreshUser} />
          </section>

          <section id="general" className="space-y-4">
            <ProfileForm user={user} refreshUser={refreshUser} />
          </section>


          
          <section id="security">
            <PasswordForm user={user} refreshUser={refreshUser} />
          </section>

          {/* Upgrade Section (Only visible if NOT a verified doctor yet) */}
          <section id="upgrade">
            {isStaffOrAdminUser(user) ? (
              <AdminExpertProfessionSection user={user} refreshUser={refreshUser} />
            ) : (
              <DoctorUpgradeSection user={user} refreshUser={refreshUser} />
            )}
          </section>
            
        </div>
      </div>
    </div>
  )
}

// --- [NEW] COMPONENT: DOCTOR PUBLIC PROFILE TRIGGER ---

function DoctorPublicProfileSection({ user }: { user: any }) {
  const [isOpen, setIsOpen] = useState(false);

  // 1. Guard Clause: Only show for doctors
  // We check role_slug (preferred) or role name fallback
  const isDoctor = hasExpertFeatures(user) && user?.expert_profession_slug !== "psychology_student";
  
  if (!isDoctor) return null;

  return (
    <>
      <div className="rounded-xl border bg-card text-card-foreground shadow-sm transition-all overflow-hidden">
        <button 
          onClick={() => setIsOpen(true)}
          className="flex items-center justify-between w-full p-6 text-start hover:bg-muted/30 transition-colors group"
        >
          <div className="flex items-center gap-4">
            <div className="p-3 bg-blue-100 dark:bg-blue-900/30 rounded-full text-blue-600 dark:text-blue-400">
              <UserCog className="w-6 h-6" />
            </div>
            <div className="flex flex-col gap-1">
              <h3 className="font-bold text-base text-foreground">پروفایل عمومی متخصص</h3>
              <p className="text-sm text-muted-foreground">
                برای نمایش در لیست متخصصین، تخصص اصلی، شهر، آدرس مطب و درباره من را کامل کنید.
              </p>
            </div>
          </div>
          
          <ChevronLeft className="w-5 h-5 text-muted-foreground group-hover:text-primary transition-colors" />
        </button>
      </div>

      {/* The Modal Component */}
      <DoctorProfileModal 
        isOpen={isOpen} 
        onOpenChange={setIsOpen} 
        onUpdate={() => {
            // Optional: You could refresh global user state here if needed, 
            // but the modal handles its own data fetching.
        }} 
      />
    </>
  )
}

function VisitorBaseProfileSection({ user, refreshUser }: { user: any, refreshUser: () => Promise<any> }) {
  const [isOpen, setIsOpen] = useState(false);
  if (!hasVisitorFeatures(user)) return null;

  return (
    <>
      <div className="rounded-xl border bg-card text-card-foreground shadow-sm transition-all overflow-hidden">
        <button
          onClick={() => setIsOpen(true)}
          className="flex items-center justify-between w-full p-6 text-start hover:bg-muted/30 transition-colors group"
        >
          <div className="flex items-center gap-4">
            <div className="p-3 bg-emerald-100 dark:bg-emerald-900/30 rounded-full text-emerald-600 dark:text-emerald-400">
              <UserCog className="w-6 h-6" />
            </div>
            <div className="flex flex-col gap-1">
              <h3 className="font-bold text-base text-foreground">پروفایل پایه مراجع</h3>
              <p className="text-sm text-muted-foreground">
                مشخصات اصلی، راه‌های ارتباطی و شبکه‌های اجتماعی خود را بدون ورود به پرونده‌ها مدیریت کنید.
              </p>
            </div>
          </div>

          <ChevronLeft className="w-5 h-5 text-muted-foreground group-hover:text-primary transition-colors" />
        </button>
      </div>

      <VisitorBaseProfileModal
        isOpen={isOpen}
        onOpenChange={setIsOpen}
        onUpdate={refreshUser}
      />
    </>
  );
}

// --- SUB-COMPONENT: DOCTOR UPGRADE (Minimal & Expandable) ---

type ExpertProfessionOption = {
  slug: string;
  name: string;
  credential_label?: string;
  credential_placeholder?: string;
  validation_kind?: string;
  university_required?: boolean;
  university_label?: string;
  university_placeholder?: string;
};

function AdminExpertProfessionSection({ user, refreshUser }: { user: any, refreshUser: () => Promise<any> }) {
  const [professions, setProfessions] = useState<ExpertProfessionOption[]>([])
  const [selectedProfession, setSelectedProfession] = useState<string>("")
  const [loadingProfessions, setLoadingProfessions] = useState(false)
  const [saving, setSaving] = useState(false)
  const selectedProfessionOption = professions.find((p) => p.slug === selectedProfession) || null;

  useEffect(() => {
    setLoadingProfessions(true)
    getExpertProfessions()
      .then((data) => {
        setProfessions(data || [])
        if (data?.length) {
          const preferredProfession = user?.expert_profession_slug || data[0].slug;
          setSelectedProfession((current) =>
            current && data.some((item) => item.slug === current) ? current : preferredProfession
          )
        }
      })
      .catch(() => toast.error("دریافت حوزه‌های تخصصی ناموفق بود"))
      .finally(() => setLoadingProfessions(false))
  }, [user?.expert_profession_slug])

  const handleSave = async () => {
    if (!selectedProfession) {
      toast.error("حوزه تخصصی را انتخاب کنید")
      return
    }

    setSaving(true)
    try {
      const res = await setAdminExpertProfession(selectedProfession)
      toast.success(res.message || "حوزه تخصصی ادمین بروزرسانی شد.")
      await refreshUser()
    } catch {
      toast.error("بروزرسانی حوزه تخصصی ناموفق بود")
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="rounded-xl border bg-card text-card-foreground shadow-sm p-4">
      <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
        <div className="flex items-start gap-3">
          <div className="rounded-full bg-primary/12 p-2 text-primary">
            <ShieldCheck className="w-4 h-4" />
          </div>
          <div className="space-y-1 text-start">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-medium">تنظیم حوزه تخصصی ادمین</span>
              <Badge variant="outline" className="border-border bg-accent text-accent-foreground">
                بدون اعتبارسنجی
              </Badge>
            </div>
            <p className="text-xs leading-6 text-muted-foreground">
              این انتخاب فقط زمینه تخصصی حساب ادمین را برای ابزارها، بوم‌ها و دسترسی‌های متخصص مشخص می‌کند.
            </p>
            <p className="text-xs text-muted-foreground">
              حوزه فعلی: {user?.expert_profession_label || "انتخاب نشده"}
            </p>
          </div>
        </div>
        <Button
          onClick={handleSave}
          disabled={saving || loadingProfessions || !selectedProfession || selectedProfession === user?.expert_profession_slug}
          size="sm"
          className="h-9 min-w-36 gap-2"
        >
          {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
          ذخیره
        </Button>
      </div>

      <div className="mt-4">
        {loadingProfessions ? (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
            در حال دریافت حوزه‌ها...
          </div>
        ) : professions.length ? (
          <div className="space-y-3">
            <Tabs value={selectedProfession} onValueChange={setSelectedProfession} className="w-full">
              <TabsList className="w-full h-auto p-1 grid grid-cols-2 gap-1 rounded-xl bg-muted/70 md:grid-cols-4">
                {professions.map((profession) => (
                  <TabsTrigger
                    key={profession.slug}
                    value={profession.slug}
                    className="h-9 rounded-lg px-2 text-[11px] md:text-sm whitespace-nowrap"
                  >
                    {profession.name}
                  </TabsTrigger>
                ))}
              </TabsList>
            </Tabs>
            {selectedProfessionOption && (
              <div className="rounded-lg border bg-background p-3 text-start text-sm">
                {selectedProfessionOption.name}
              </div>
            )}
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">حوزه تخصصی فعالی برای انتخاب وجود ندارد.</p>
        )}
      </div>
    </div>
  )
}

function DoctorUpgradeSection({ user, refreshUser }: { user: any, refreshUser: () => Promise<any> }) {
  const [isOpen, setIsOpen] = useState(false)
  const [isWelcomeOpen, setIsWelcomeOpen] = useState(false)
  const [welcomeProfession, setWelcomeProfession] = useState("")
  const [credentialCode, setCredentialCode] = useState("")
  const [nationalCode, setNationalCode] = useState("")
  const [universityName, setUniversityName] = useState("")
  const [professions, setProfessions] = useState<ExpertProfessionOption[]>([])
  const [selectedProfession, setSelectedProfession] = useState<string>("")
  const [loading, setLoading] = useState(false)
  
  const isVerified = user?.is_expert_verified;
  const verificationStatus = user?.expert_verification_status || (isVerified ? "approved" : "none");
  const isPendingReview = verificationStatus === "pending";
  const selectedProfessionOption = professions.find((p) => p.slug === selectedProfession) || null;
  const credentialLabel = selectedProfessionOption?.credential_label || "کد اعتبارسنجی تخصص";
  const credentialPlaceholder = selectedProfessionOption?.credential_placeholder || "کد اعتبارسنجی تخصص را وارد کنید";
  const submittedCode = user?.medical_license || "";
  const requestMessage = user?.expert_verification_message || "";

  const normalizeNationalCodeDigits = (value: string) =>
    value
      .replace(/[۰-۹]/g, (d) => String("۰۱۲۳۴۵۶۷۸۹".indexOf(d)))
      .replace(/[٠-٩]/g, (d) => String("٠١٢٣٤٥٦٧٨٩".indexOf(d)))
      .replace(/\D/g, "")
      .slice(0, 10);

  const isValidIranianNationalCode = (value: string) => {
    const code = normalizeNationalCodeDigits(value);
    if (code.length !== 10) return false;
    if (/^(\d)\1{9}$/.test(code)) return false;
    const check = Number(code[9]);
    const sum = code
      .slice(0, 9)
      .split("")
      .reduce((acc, digit, idx) => acc + Number(digit) * (10 - idx), 0);
    const remainder = sum % 11;
    return remainder < 2 ? check === remainder : check === 11 - remainder;
  };

  useEffect(() => {
    if (!isOpen) return;
    getExpertProfessions()
      .then((data) => {
        setProfessions(data || []);
        if (data?.length) {
          const preferredProfession = user?.expert_profession_slug || data[0].slug;
          if (!selectedProfession || !data.some((item) => item.slug === selectedProfession)) {
            setSelectedProfession(preferredProfession);
          }
        }
      })
      .catch(() => toast.error("دریافت حوزه‌های تخصصی ناموفق بود"));
  }, [isOpen, selectedProfession, user?.expert_profession_slug]);

  useEffect(() => {
    if (user?.national_code) {
      setNationalCode(user.national_code);
    }
  }, [user?.national_code]);

  const formatSubmittedAt = (value?: string | null) => {
    if (!value) return "نامشخص";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "نامشخص";
    return new Intl.DateTimeFormat("fa-IR", {
      year: "numeric",
      month: "long",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(date);
  };

  const handleVerify = async () => {
    if (!selectedProfession) {
      toast.error("حوزه تخصصی را انتخاب کنید");
      return;
    }
    if (!credentialCode || credentialCode.length < 3) {
        toast.error("کد معتبر نیست");
        return;
    }
    if (!isValidIranianNationalCode(nationalCode)) {
      toast.error("کد ملی معتبر نیست");
      return;
    }
    if (selectedProfessionOption?.university_required && universityName.trim().length < 2) {
      toast.error("نام دانشگاه را وارد کنید");
      return;
    }

    setLoading(true)
    try {
      const res = await upgradeExpert(
        user?.full_name || "",
        selectedProfession,
        credentialCode,
        normalizeNationalCodeDigits(nationalCode),
        universityName.trim()
      )
      toast.success(res.message || "درخواست شما ثبت شد.")
      await refreshUser()
      setWelcomeProfession(res.profession_label || selectedProfessionOption?.name || "متخصص")
      setIsWelcomeOpen(true)
      setIsOpen(false)
      setCredentialCode("")
      setUniversityName("")
      setNationalCode(normalizeNationalCodeDigits(nationalCode))
    } catch(e) {
      toast.error("اعتبارسنجی ناموفق بود")
    } finally {
      setLoading(false)
    }
  }

  if (verificationStatus === "approved") {
    return (
      <>
        <Dialog open={isWelcomeOpen} onOpenChange={setIsWelcomeOpen}>
          <DialogContent dir="rtl" className="max-w-sm">
            <DialogHeader className="text-right">
              <DialogTitle>به نقش {welcomeProfession} خوش آمدید</DialogTitle>
              <DialogDescription className="leading-6">
                برای تجربه بهتر، این اپ را با لپ‌تاپ یا تبلت باز کنید. نسخه موبایل هم در دسترس است.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button onClick={() => setIsWelcomeOpen(false)} className="w-full sm:w-auto">
                متوجه شدم
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        <div className="rounded-xl border bg-card text-card-foreground shadow-sm p-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="rounded-full bg-primary/12 p-2 text-primary">
              <BadgeCheck className="w-4 h-4" />
            </div>
            <div className="flex flex-col">
              <span className="text-sm font-medium">حساب متخصص شما فعال است</span>
              <span className="text-xs text-muted-foreground">
                حوزه تایید شده: {user?.expert_profession_label || "نامشخص"}
              </span>
            </div>
          </div>
          <Badge variant="outline" className="border-border bg-accent text-accent-foreground">
            تایید شده
          </Badge>
        </div>
      </>
    );
  }

  return (
    <>
      <Dialog open={isWelcomeOpen} onOpenChange={setIsWelcomeOpen}>
        <DialogContent dir="rtl" className="max-w-sm">
          <DialogHeader className="text-right">
            <DialogTitle>به نقش {welcomeProfession} خوش آمدید</DialogTitle>
            <DialogDescription className="leading-6">
              برای تجربه بهتر، این اپ را با لپ‌تاپ یا تبلت باز کنید. نسخه موبایل هم در دسترس است.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button onClick={() => setIsWelcomeOpen(false)} className="w-full sm:w-auto">
              متوجه شدم
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <div className="rounded-xl border bg-card text-card-foreground shadow-sm transition-all overflow-hidden">
        {/* Trigger Row */}
        <button 
          onClick={() => setIsOpen(!isOpen)}
          className="flex items-center justify-between w-full p-4 text-start hover:bg-muted/30 transition-colors"
        >
          <div className="flex items-center gap-3">
            <div className="p-2 bg-muted rounded-full text-muted-foreground">
              <Stethoscope className="w-4 h-4" />
            </div>
            <div className="flex flex-col gap-0.5">
              <span className="text-sm font-medium">ارتقا به حساب متخصص</span>
              <span className="text-xs text-muted-foreground">
                حوزه تخصصی خود را تایید کنید تا دسترسی متخصص برای شما فعال شود.
              </span>
            </div>
          </div>
          <ChevronDown className={cn("w-4 h-4 text-muted-foreground transition-transform duration-200", isOpen && "rotate-180")} />
        </button>

        {/* Expanded Content */}
        <AnimatePresence>
          {isOpen && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.2 }}
            >
              <div className="px-4 pb-4 pt-0 border-t border-dashed bg-muted/10">
                <div className="space-y-3 py-3">
                  {isPendingReview && (
                    <div className="rounded-xl border border-border bg-accent/40 p-4">
                      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                        <div className="flex items-start gap-3">
                          <div className="mt-0.5 rounded-full bg-primary/12 p-2 text-primary">
                            <Clock3 className="h-4 w-4" />
                          </div>
                          <div className="space-y-2 text-start">
                            <div className="flex flex-wrap items-center gap-2">
                              <span className="text-sm font-semibold text-foreground">درخواست شما در انتظار بررسی است</span>
                              <Badge variant="outline" className="border-border bg-background text-foreground">
                                در انتظار تایید
                              </Badge>
                            </div>
                            <p className="text-xs leading-6 text-muted-foreground">
                              {requestMessage || "اطلاعات شما ثبت شده است. پس از بررسی و تایید، حساب متخصص برای شما فعال می‌شود."}
                            </p>
                            <div className="grid gap-2 text-xs text-foreground md:grid-cols-3">
                              <div className="rounded-lg border border-border bg-card px-3 py-2">
                                <div className="text-[11px] text-muted-foreground">حوزه ثبت‌شده</div>
                                <div className="mt-1 font-medium">{user?.expert_profession_label || "نامشخص"}</div>
                              </div>
                              <div className="rounded-lg border border-border bg-card px-3 py-2">
                                <div className="text-[11px] text-muted-foreground">زمان ارسال</div>
                                <div className="mt-1 font-medium">{formatSubmittedAt(user?.expert_verification_requested_at)}</div>
                              </div>
                              <div className="rounded-lg border border-border bg-card px-3 py-2">
                                <div className="text-[11px] text-muted-foreground">کد ثبت‌شده</div>
                                <div className="mt-1 font-mono font-medium">{submittedCode || "نامشخص"}</div>
                              </div>
                            </div>
                          </div>
                        </div>
                      </div>
                    </div>
                  )}
                  <p className="text-xs text-muted-foreground leading-relaxed">
                    {isPendingReview
                      ? "در صورت نیاز به اصلاح اطلاعات یا ارسال دوباره، حوزه تخصصی را انتخاب کنید و اطلاعات جدید را ثبت کنید."
                      : "حوزه تخصصی را انتخاب کنید و اطلاعات اعتبارسنجی همان حوزه را وارد کنید."}
                  </p>
                </div>

                <div className="grid gap-3 w-full">
                  {!!professions.length && (
                    <Tabs value={selectedProfession} onValueChange={setSelectedProfession} className="w-full">
                      <TabsList className="w-full h-auto p-1 grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-1 rounded-xl bg-muted/70">
                        {professions.map((profession) => (
                          <TabsTrigger
                            key={profession.slug}
                            value={profession.slug}
                            className="h-9 rounded-lg px-2 text-[11px] md:text-sm whitespace-nowrap"
                          >
                            {profession.name}
                          </TabsTrigger>
                        ))}
                      </TabsList>
                    </Tabs>
                  )}
                  {selectedProfessionOption && (
                    <div className="rounded-lg border bg-background p-3 space-y-2">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="text-sm font-medium">{selectedProfessionOption.name}</div>
                        {selectedProfessionOption.validation_kind?.startsWith("manual_") && (
                          <Badge variant="outline" className="border-border text-primary">
                            بررسی دستی
                          </Badge>
                        )}
                      </div>
                    </div>
                  )}
                  <div className="space-y-1.5">
                    <Label htmlFor="expert-credential-input" className="text-xs text-muted-foreground">
                      {credentialLabel}
                    </Label>
                    <Input
                      id="expert-credential-input"
                      value={credentialCode}
                      onChange={(e) => setCredentialCode(e.target.value)}
                      placeholder={credentialPlaceholder}
                      className="bg-background text-center font-mono h-9 text-sm"
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="expert-national-code-input" className="text-xs text-muted-foreground">
                      کد ملی
                    </Label>
                    <Input
                      id="expert-national-code-input"
                      inputMode="numeric"
                      maxLength={10}
                      value={nationalCode}
                      onChange={(e) => setNationalCode(normalizeNationalCodeDigits(e.target.value))}
                      placeholder="مثال: ۰۰۱۲۳۴۵۶۷۸"
                      className="bg-background text-center font-mono h-9 text-sm"
                    />
                  </div>
                  {selectedProfessionOption?.university_required && (
                    <div className="space-y-1.5">
                      <Label htmlFor="expert-university-input" className="text-xs text-muted-foreground">
                        {selectedProfessionOption.university_label || "نام دانشگاه"}
                      </Label>
                      <Input
                        id="expert-university-input"
                        value={universityName}
                        onChange={(e) => setUniversityName(e.target.value)}
                        placeholder={selectedProfessionOption.university_placeholder || "نام دانشگاه محل تحصیل را وارد کنید"}
                        className="bg-background h-9 text-sm"
                      />
                    </div>
                  )}
                  <Button 
                    onClick={handleVerify} 
                    disabled={loading} 
                    size="sm"
                    className="bg-primary hover:bg-primary/90 text-primary-foreground h-9 px-5 w-full md:w-auto md:self-end md:min-w-44"
                  >
                      {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : isPendingReview ? "ارسال مجدد درخواست" : "بررسی و ثبت درخواست"}
                  </Button>
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </>
  )
}

// --- SUB-COMPONENT: PROFILE FORM ---

function ProfileForm({ user, refreshUser }: { user: any, refreshUser: () => Promise<any> }) {
  const [isLoading, setIsLoading] = useState(false)
  const [success, setSuccess] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const [formData, setFormData] = useState({
    full_name: "",
    email: "",
  })

  useEffect(() => {
    if (user) {
      setFormData({
        full_name: user.full_name || "",
        email: user.email || "",
      })
    }
  }, [user])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setIsLoading(true)
    setSuccess(null)
    setError(null)

    const headers = getAuthHeaders()
    if (!headers.Authorization) return

    try {
      const res = await fetch(`${API_BASE_URL}/api/auth/profile/`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json", ...headers },
        body: JSON.stringify(formData),
      })

      if (!res.ok) throw new Error("خطا در بروزرسانی پروفایل.")

      await refreshUser()
      setSuccess("پروفایل با موفقیت بروزرسانی شد.")
      setTimeout(() => setSuccess(null), 3000)
    } catch (e) {
      setError("مشکلی پیش آمد. لطفاً دوباره تلاش کنید.")
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <Card>
      <CardHeader className="text-start">
        <div className="flex items-center gap-2">
          <div className="p-2 bg-primary/10 rounded-md text-primary">
            <User className="h-5 w-5" />
          </div>
          <div>
            <CardTitle>اطلاعات کاربری</CardTitle>
            <CardDescription>اطلاعات شخصی خود را بروزرسانی کنید.</CardDescription>
          </div>
        </div>
      </CardHeader>
      <Separator />
      <form onSubmit={handleSubmit}>
        <CardContent className="space-y-6 pt-6 text-start">
          {success && (
            <Alert className="bg-green-50 text-green-800 border-green-200">
              <CheckCircle2 className="h-4 w-4" />
              <AlertTitle>موفق</AlertTitle>
              <AlertDescription>{success}</AlertDescription>
            </Alert>
          )}
          {error && (
            <Alert variant="destructive">
              <AlertCircle className="h-4 w-4" />
              <AlertTitle>خطا</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}

          <div className="grid gap-6 md:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="phone">شماره موبایل</Label>
              <Input 
                id="phone" 
                value={user?.phone_number || ""} 
                disabled 
                className="bg-muted text-muted-foreground text-left ltr" 
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="name">نام و نام خانوادگی</Label>
              <Input 
                id="name" 
                value={formData.full_name} 
                onChange={(e) => setFormData({...formData, full_name: e.target.value})} 
                placeholder="مثال: علی محمدی"
              />
            </div>

            <div className="space-y-2 md:col-span-2">
              <Label htmlFor="email">آدرس ایمیل</Label>
              <Input 
                id="email" 
                type="email"
                value={formData.email} 
                onChange={(e) => setFormData({...formData, email: e.target.value})} 
                placeholder="ali@example.com"
                className="text-left ltr"
              />
              <p className="text-xs text-muted-foreground">
                وارد کردن ایمیل اختیاری است و هر زمان بخواهید می‌توانید آن را ثبت یا ویرایش کنید.
              </p>
            </div>
          </div>
        </CardContent>
        <CardFooter className="px-6 py-4 bg-muted/5 rounded-b-xl flex justify-end">
          <Button type="submit" disabled={isLoading} className="gap-2">
            {isLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            ذخیره تغییرات
          </Button>
        </CardFooter>
      </form>
    </Card>
  )
}

// --- SUB-COMPONENT: PASSWORD FORM ---

function PasswordForm({ user, refreshUser }: { user: any, refreshUser: () => Promise<any> }) {
  const [isLoading, setIsLoading] = useState(false)
  const [success, setSuccess] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const hasPassword = user?.has_password !== false

  const [passwords, setPasswords] = useState({
    old_password: "",
    new_password: "",
    confirm_password: "",
  })

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setIsLoading(true)
    setSuccess(null)
    setError(null)

    if (passwords.new_password !== passwords.confirm_password) {
      setError("رمزهای عبور جدید مطابقت ندارند.")
      setIsLoading(false)
      return
    }

    if (hasPassword && !passwords.old_password) {
      setError("لطفاً رمز عبور فعلی را وارد کنید.")
      setIsLoading(false)
      return
    }

    const headers = getAuthHeaders()
    if (!headers.Authorization) {
      setError("برای تغییر رمز عبور باید وارد حساب شوید.")
      setIsLoading(false)
      return
    }

    try {
      const res = await fetch(`${API_BASE_URL}/api/auth/change-password/`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...headers },
        body: JSON.stringify({
          old_password: passwords.old_password,
          new_password: passwords.new_password,
          confirm_password: passwords.confirm_password,
        }),
      })

      const data = await res.json()

      if (!res.ok) {
        const msg = typeof data === 'object' ? Object.values(data).flat().join(" ") : "خطا در تغییر رمز."
        throw new Error(msg)
      }

      await refreshUser()
      setSuccess(hasPassword ? "رمز عبور با موفقیت تغییر یافت." : "رمز عبور اولیه با موفقیت تنظیم شد.")
      setPasswords({ old_password: "", new_password: "", confirm_password: "" })
      setTimeout(() => setSuccess(null), 3000)
    } catch (e: any) {
      setError(e.message || "تغییر رمز عبور با خطا مواجه شد.")
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <Card>
      <CardHeader className="text-start">
        <div className="flex items-center gap-2">
          <div className="p-2 bg-primary/10 rounded-md text-primary">
            <ShieldCheck className="h-5 w-5" />
          </div>
          <div>
            <CardTitle>امنیت</CardTitle>
            <CardDescription>از امنیت حساب خود اطمینان حاصل کنید.</CardDescription>
          </div>
        </div>
      </CardHeader>
      <Separator />
      <form onSubmit={handleSubmit}>
        <CardContent className="space-y-6 pt-6 text-start">
          {success && (
            <Alert className="bg-muted-50 text-green-400 border-muted-200">
              <CheckCircle2 className="h-4 w-4" />
              <AlertTitle>موفق</AlertTitle>
              <AlertDescription>{success}</AlertDescription>
            </Alert>
          )}
          {error && (
            <Alert variant="destructive">
              <AlertCircle className="h-4 w-4" />
              <AlertTitle>خطا</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}

          <div className="grid gap-6 md:grid-cols-2">
            {hasPassword ? (
              <div className="space-y-2 md:col-span-2">
                <Label htmlFor="old">رمز عبور فعلی</Label>
                <Input 
                  id="old" 
                  type="password"
                  value={passwords.old_password}
                  onChange={(e) => setPasswords({...passwords, old_password: e.target.value})}
                  required
                  className="text-left ltr"
                />
              </div>
            ) : (
              <Alert className="md:col-span-2 bg-muted-50 text-blue-400 border-muted-200">
                <AlertCircle className="h-4 w-4" />
                <AlertTitle>تنظیم رمز اولیه</AlertTitle>
                <AlertDescription>
                  برای حساب شما رمز عبور فعلی ثبت نشده است. رمز عبور جدید را برای فعال‌سازی ورود با رمز تعیین کنید.
                </AlertDescription>
              </Alert>
            )}

            <div className="space-y-2">
              <Label htmlFor="new">رمز عبور جدید</Label>
              <Input 
                id="new" 
                type="password"
                value={passwords.new_password}
                onChange={(e) => setPasswords({...passwords, new_password: e.target.value})}
                required
                minLength={8}
                className="text-left ltr"
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="confirm">تکرار رمز عبور جدید</Label>
              <Input 
                id="confirm" 
                type="password"
                value={passwords.confirm_password}
                onChange={(e) => setPasswords({...passwords, confirm_password: e.target.value})}
                required
                className="text-left ltr"
              />
            </div>
          </div>
        </CardContent>
        <CardFooter className="px-6 py-4 bg-muted/5 rounded-b-xl flex justify-end">
          <Button type="submit" disabled={isLoading} className="gap-2">
            {isLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Lock className="h-4 w-4" />}
            {hasPassword ? "تغییر رمز عبور" : "ثبت رمز عبور"}
          </Button>
        </CardFooter>
      </form>
    </Card>
  )
}
