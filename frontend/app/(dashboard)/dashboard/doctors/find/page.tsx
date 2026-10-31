"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { 
  Search, 
  MapPin, 
  DollarSign, 
  CalendarPlus, 
  FileText,
  Loader2, 
  AlertCircle,
  ArrowRight,
  Filter
} from "lucide-react";
import { toast } from "sonner";

import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardFooter } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { 
  Command, 
  CommandEmpty, 
  CommandGroup, 
  CommandInput, 
  CommandItem, 
  CommandList 
} from "@/components/ui/command";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

import { API_BASE_URL, getAuthHeaders, getExpertProfessions } from "@/lib/api";
import { fetchAllLocationOptions, parseLocationName, sortLocationsForPicker } from "@/lib/location-utils";
import { fixAvatarUrl } from "@/lib/utils";

interface Location {
    id: number;
    name: string;
}

interface Doctor {
  id: number;
  full_name: string;
  specialty: string;
  location_name: string | null;
  bio: string;
  clinic_address: string;
  avatar: string | null;
  meeting_price: string;
  accepting_new_patients: boolean;
  expert_profession_slug?: string | null;
  expert_profession_label?: string | null;
}

interface ProfessionOption {
  slug: string;
  name: string;
}

const ONES = ["", "یک", "دو", "سه", "چهار", "پنج", "شش", "هفت", "هشت", "نه"];
const TEENS = ["ده", "یازده", "دوازده", "سیزده", "چهارده", "پانزده", "شانزده", "هفده", "هجده", "نوزده"];
const TENS = ["", "", "بیست", "سی", "چهل", "پنجاه", "شصت", "هفتاد", "هشتاد", "نود"];
const HUNDREDS = ["", "صد", "دویست", "سیصد", "چهارصد", "پانصد", "ششصد", "هفتصد", "هشتصد", "نهصد"];
const EN_NUMBER_FORMATTER = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const DIRECTORY_PAGE_SIZE = 24;
const FA_NUMBER_FORMATTER = new Intl.NumberFormat("fa-IR");

function tripletToPersianWords(num: number): string {
  if (num === 0) return "";

  const parts: string[] = [];
  const hundred = Math.floor(num / 100);
  const remainder = num % 100;

  if (hundred > 0) {
    parts.push(HUNDREDS[hundred]);
  }

  if (remainder >= 10 && remainder <= 19) {
    parts.push(TEENS[remainder - 10]);
  } else {
    const ten = Math.floor(remainder / 10);
    const one = remainder % 10;
    if (ten > 0) parts.push(TENS[ten]);
    if (one > 0) parts.push(ONES[one]);
  }

  return parts.join(" و ");
}

function numberToPersianWords(num: number): string {
  if (num === 0) return "صفر";

  const scales = ["", "هزار", "میلیون", "میلیارد", "تریلیون"];
  const parts: string[] = [];
  let remaining = num;
  let scaleIndex = 0;

  while (remaining > 0 && scaleIndex < scales.length) {
    const chunk = remaining % 1000;
    if (chunk > 0) {
      const words = tripletToPersianWords(chunk);
      const scale = scales[scaleIndex];
      parts.unshift(scale ? `${words} ${scale}` : words);
    }

    remaining = Math.floor(remaining / 1000);
    scaleIndex += 1;
  }

  return parts.join(" و ");
}

function getMeetingPriceDisplay(value: string | number | null | undefined): { full: string; words: string } | null {
  if (value === null || value === undefined || value === "") return null;

  const parsed = typeof value === "string" ? Number(value) : value;
  if (!Number.isFinite(parsed) || parsed <= 0) return null;

  const rounded = Math.round(parsed);
  return {
    full: `${EN_NUMBER_FORMATTER.format(rounded)} تومان`,
    words: `${numberToPersianWords(rounded)} تومان`,
  };
}

export default function FindDoctorPage() {
  // ... (State logic same as before) ...
  const [doctors, setDoctors] = useState<Doctor[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [totalCount, setTotalCount] = useState(0);
  const [hasNextPage, setHasNextPage] = useState(false);
  const [hasPreviousPage, setHasPreviousPage] = useState(false);
  const totalPages = Math.max(1, Math.ceil(totalCount / DIRECTORY_PAGE_SIZE));
  const resultsRef = useRef<HTMLDivElement>(null);

  const goToPage = (nextPage: number) => {
    setPage(nextPage);
    resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  
  const [search, setSearch] = useState("");
  const [locations, setLocations] = useState<Location[]>([]);
  const [selectedLocationIds, setSelectedLocationIds] = useState<number[]>([]);
  const [selectedProfession, setSelectedProfession] = useState<string>("ALL");
  const [professionOptions, setProfessionOptions] = useState<ProfessionOption[]>([]);
  
  const [selectedDoctor, setSelectedDoctor] = useState<Doctor | null>(null);
  const [profileDoctor, setProfileDoctor] = useState<Doctor | null>(null);
  const [isRequesting, setIsRequesting] = useState(false);
  const sortedLocations = sortLocationsForPicker(locations);
  const selectedLocations = sortedLocations.filter((loc) => selectedLocationIds.includes(loc.id));
  
  const [formData, setFormData] = useState({
    main_concern: "",
    history_brief: "",
    preferred_time: ""
  });

  // Fetch Locations
  useEffect(() => {
    fetchAllLocationOptions<Location>(`${API_BASE_URL}/api/vania/locations/`, getAuthHeaders())
        .then((list) => {
            setLocations(list);
        })
        .catch(err => {
            console.error("Location fetch error:", err);
            setLocations([]);
        });
  }, []);

  useEffect(() => {
    getExpertProfessions()
      .then((items) => setProfessionOptions(items || []))
      .catch(() => setProfessionOptions([]));
  }, []);

  // Cancel older searches so their responses cannot overwrite the current page.
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    const fetchDoctors = async () => {
      try {
        const params = new URLSearchParams();
        params.set("page", String(page));
        if (search) params.append("search", search);
        selectedLocationIds.forEach(id => {
            params.append("locations", String(id));
        });
        if (selectedProfession && selectedProfession !== "ALL") {
          params.append("profession", selectedProfession);
        }

        const res = await fetch(`${API_BASE_URL}/api/vania/experts/?${params.toString()}`, {
          headers: getAuthHeaders(),
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        if (res.status === 404 && page > 1) {
          setPage(1);
          return;
        }
        if (!res.ok) throw new Error("Failed to fetch doctors list.");
      
        const data = await res.json();
        const list = Array.isArray(data) ? data : data.results || [];
        if (controller.signal.aborted) return;
        setDoctors(list);
        setTotalCount(Array.isArray(data) ? list.length : data.count ?? list.length);
        setHasNextPage(!Array.isArray(data) && Boolean(data.next));
        setHasPreviousPage(!Array.isArray(data) && Boolean(data.previous));
      } catch (e: any) {
        if (controller.signal.aborted) return;
        console.error(e);
        setError("خطا در دریافت لیست متخصصین");
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    };
    const timer = setTimeout(fetchDoctors, 300);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [search, selectedLocationIds, selectedProfession, page]);

  const toggleLocation = (id: number) => {
    setPage(1);
    setSelectedLocationIds(prev => 
        prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]
    );
  };

  const handleSubmitRequest = async () => {
    if (!selectedDoctor) return;
    if (!formData.main_concern) {
        toast.error("لطفاً علت اصلی مراجعه خود را بنویسید.");
        return;
    }

    setIsRequesting(true);
    try {
      const res = await fetch(`${API_BASE_URL}/api/vania/experts/${selectedDoctor.id}/request/`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...getAuthHeaders() },
        body: JSON.stringify(formData)
      });
      
      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.error || "خطا در ارسال درخواست.");
      }
      
      toast.success(data.message || "درخواست شما با موفقیت ثبت و برای متخصص ارسال شد.");
      setSelectedDoctor(null); 
      setFormData({ main_concern: "", history_brief: "", preferred_time: "" }); 
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setIsRequesting(false);
    }
  };

  return (
    <div className="flex flex-col w-full h-full space-y-8 pb-10 max-w-6xl mx-auto pt-6" dir="rtl">
      
      {/* Header */}
      <div className="flex flex-col gap-4">
        <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" asChild className="px-0 hover:bg-transparent">
                <Link href="/dashboard/experts" className="flex items-center gap-1 text-muted-foreground hover:text-primary">
                    <ArrowRight className="h-4 w-4" /> بازگشت
                </Link>
            </Button>
        </div>
        <div className="flex flex-col gap-1">
            <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
            <Search className="h-6 w-6 text-primary" />
            جستجوی متخصص جدید
            </h1>
            <p className="text-muted-foreground text-sm">
            مشاهده پروفایل متخصصین و ارسال درخواست مشاوره.
            </p>
        </div>
      </div>

      {/* Search & Filter Bar */}
      <div className="flex flex-col md:flex-row gap-3">
        <div className="relative flex-1">
            <Search className="absolute right-3 top-3 h-4 w-4 text-muted-foreground" />
            <Input 
              placeholder="جستجوی نام متخصص یا تخصص..." 
              className="pr-9 bg-background"
              value={search}
              onChange={(e) => { setSearch(e.target.value); setPage(1); }}
            />
        </div>

        <Select value={selectedProfession} onValueChange={(value) => { setSelectedProfession(value); setPage(1); }}>
          <SelectTrigger className="w-full md:w-[220px]">
            <SelectValue placeholder="حوزه تخصصی" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="ALL">همه حوزه‌ها</SelectItem>
            {professionOptions.map((option) => (
              <SelectItem key={option.slug} value={option.slug}>
                {option.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Popover>
            <PopoverTrigger asChild>
                <Button variant="outline" className="border-dashed flex items-center gap-2 h-10 min-w-[160px] justify-start">
                    <Filter className="h-4 w-4" />
                    استان / شهر
                    {selectedLocationIds.length > 0 && (
                        <>
                            <Separator orientation="vertical" className="mx-1 h-4" />
                            <Badge variant="secondary" className="rounded-sm px-1 font-normal lg:hidden">
                                {selectedLocationIds.length}
                            </Badge>
                            <div className="hidden lg:flex gap-1 overflow-hidden">
                                {selectedLocationIds.length > 2 ? (
                                    <Badge variant="secondary" className="rounded-sm px-1 font-normal">
                                        {selectedLocationIds.length} انتخاب شده
                                    </Badge>
                                ) : (
                                    selectedLocations
                                        .map(l => (
                                            <Badge variant="secondary" key={l.id} className="rounded-sm px-1 font-normal">
                                                {parseLocationName(l.name).label}
                                            </Badge>
                                        ))
                                )}
                            </div>
                        </>
                    )}
                </Button>
            </PopoverTrigger>
            <PopoverContent
                className="w-[calc(100vw-1rem)] max-w-[min(22rem,var(--radix-popover-available-width))] p-0 sm:w-[22rem]"
                align="end"
                dir="rtl"
                collisionPadding={8}
                sideOffset={6}
                onWheelCapture={(event) => event.stopPropagation()}
            >
                <Command className="text-right">
                    <CommandInput className="text-right" placeholder="جستجوی استان یا شهر..." />
                    <CommandList className="max-h-none overflow-visible">
                        <CommandEmpty>نتیجه‌ای یافت نشد.</CommandEmpty>
                        <ScrollArea className="h-[min(24rem,70vh)]" onWheelCapture={(event) => event.stopPropagation()}>
                            <CommandGroup>
                                {sortedLocations.map((loc) => {
                                    const parsedLocation = parseLocationName(loc.name);

                                    return (
                                        <CommandItem
                                            key={loc.id}
                                            value={parsedLocation.searchValue}
                                            onSelect={() => toggleLocation(loc.id)}
                                            className="cursor-pointer flex-row-reverse text-right"
                                        >
                                            <div className={`mr-2 flex h-4 w-4 items-center justify-center rounded-sm border border-primary ${selectedLocationIds.includes(loc.id) ? "bg-primary text-primary-foreground" : "opacity-50 [&_svg]:invisible"}`}>
                                                <svg className="h-3 w-3" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12" /></svg>
                                            </div>
                                            <div className="flex min-w-0 flex-1 flex-row-reverse items-center justify-between gap-3 text-right">
                                                <span className="truncate text-right">{parsedLocation.label}</span>
                                                {!parsedLocation.isProvince && (
                                                    <span className="truncate text-[11px] text-muted-foreground">
                                                        شهر
                                                    </span>
                                                )}
                                            </div>
                                        </CommandItem>
                                    );
                                })}
                            </CommandGroup>
                        </ScrollArea>
                        {selectedLocationIds.length > 0 && (
                            <>
                                <Separator />
                                <CommandGroup>
                                    <CommandItem
                                        onSelect={() => { setSelectedLocationIds([]); setPage(1); }}
                                        className="justify-center text-center cursor-pointer font-medium text-destructive"
                                    >
                                        پاک کردن فیلترها
                                    </CommandItem>
                                </CommandGroup>
                            </>
                        )}
                    </CommandList>
                </Command>
            </PopoverContent>
        </Popover>
      </div>

      {/* Content Area */}
      <div ref={resultsRef} className="scroll-mt-4" />
      {loading ? (
        <div className="h-40 flex items-center justify-center text-muted-foreground gap-2">
            <Loader2 className="h-6 w-6 animate-spin" /> در حال جستجو...
        </div>
      ) : error ? (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>خطا</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : doctors.length === 0 ? (
        <div className="h-40 flex flex-col items-center justify-center text-muted-foreground bg-muted/10 border-2 border-dashed rounded-xl gap-2">
            <Search className="h-8 w-8 opacity-20" />
            <span>متخصصی با این مشخصات یافت نشد.</span>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 animate-in fade-in">
          {doctors.map(doc => (
            <Card key={doc.id} className="hover:shadow-lg hover:-translate-y-1 transition-all flex flex-col">
              <CardHeader className="flex flex-row items-center gap-4 pb-3">
                <Avatar className="h-16 w-16 border-2 border-background shadow-md">
                  {/* [FIX] Apply fixAvatarUrl here */}
                  <AvatarImage src={fixAvatarUrl(doc.avatar) || ""} alt={doc.full_name} className="object-cover" />
                  <AvatarFallback className="bg-primary/10 text-primary text-xl">
                    {doc.full_name ? doc.full_name.slice(0,1) : "D"}
                  </AvatarFallback>
                </Avatar>
                <div className="space-y-1.5 min-w-0">
                  <CardTitle className="text-base truncate">{doc.full_name || "دکتر ناشناس"}</CardTitle>
                  <div className="flex flex-col gap-1 items-start">
                    <Badge variant="secondary" className="font-normal text-xs">{doc.specialty}</Badge>
                    {doc.expert_profession_label && (
                      <Badge variant="outline" className="font-normal text-xs">
                        {doc.expert_profession_label}
                      </Badge>
                    )}
                    {doc.location_name && (
                         <span className="text-[10px] text-muted-foreground flex items-center gap-1">
                             <MapPin className="h-3 w-3" /> {parseLocationName(doc.location_name).label}
                         </span>
                     )}
                  </div>
                </div>
              </CardHeader>
              
              <CardContent className="flex-1 space-y-3 text-sm pt-2">
                <p className="text-muted-foreground line-clamp-3 min-h-[3.75em] leading-relaxed">
                    {doc.bio || "توضیحات تکمیلی ثبت نشده است."}
                </p>
                
                <div className="space-y-1.5 text-xs text-foreground/80 pt-2 border-t border-dashed">
                    {doc.clinic_address && (
                        <div className="flex items-start gap-2">
                            <MapPin className="h-3.5 w-3.5 mt-0.5 text-muted-foreground shrink-0" />
                            <span className="line-clamp-1">{doc.clinic_address}</span>
                        </div>
                    )}
                    {(() => {
                      const meetingPrice = getMeetingPriceDisplay(doc.meeting_price);
                      if (!meetingPrice) return null;

                      return (
                        <div className="space-y-1">
                          <div className="flex items-center gap-2">
                            <DollarSign className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                            <span>هزینه جلسه: {meetingPrice.full}</span>
                          </div>
                          <p className="text-muted-foreground pr-5">{meetingPrice.words}</p>
                        </div>
                      );
                    })()}
                </div>
              </CardContent>

              <CardFooter className="pt-0 flex-col gap-2">
                <Button 
                    className="w-full gap-2" 
                    onClick={() => setSelectedDoctor(doc)}
                    disabled={!doc.accepting_new_patients} 
                    variant={doc.accepting_new_patients ? "default" : "secondary"}
                >
                    {doc.accepting_new_patients ? (
                        <>
                            <CalendarPlus className="h-4 w-4" /> درخواست نوبت
                        </>
                    ) : (
                        "عدم پذیرش مراجع جدید"
                    )}
                </Button>
                <Button
                  variant="outline"
                  className="w-full gap-2"
                  onClick={() => setProfileDoctor(doc)}
                >
                  <FileText className="h-4 w-4" />
                  مشاهده اطلاعات کامل متخصص
                </Button>
              </CardFooter>
            </Card>
          ))}
        </div>
      )}

      {!error && totalCount > 0 && (
        <nav aria-label="صفحه‌بندی متخصصان" className="flex flex-col items-center justify-between gap-4 sm:flex-row">
          <p className="text-sm text-muted-foreground" aria-live="polite">
            {loading ? "در حال دریافت نتایج..." : `نمایش ${FA_NUMBER_FORMATTER.format((page - 1) * DIRECTORY_PAGE_SIZE + 1)} تا ${FA_NUMBER_FORMATTER.format(Math.min(page * DIRECTORY_PAGE_SIZE, totalCount))} از ${FA_NUMBER_FORMATTER.format(totalCount)} متخصص`}
          </p>
          <div className="flex items-center gap-3">
            <Button variant="outline" disabled={loading || !hasPreviousPage} onClick={() => goToPage(Math.max(1, page - 1))}>
              قبلی
            </Button>
            <span className="text-sm" aria-current="page">
              صفحهٔ {FA_NUMBER_FORMATTER.format(page)} از {FA_NUMBER_FORMATTER.format(totalPages)}
            </span>
            <Button variant="outline" disabled={loading || !hasNextPage} onClick={() => goToPage(page + 1)}>
              بعدی
            </Button>
          </div>
        </nav>
      )}

      {/* --- Request Modal --- */}
      <Dialog open={!!selectedDoctor} onOpenChange={(o) => !o && setSelectedDoctor(null)}>
        <DialogContent className="sm:max-w-md" dir="rtl">
            <DialogHeader className="text-right">
                <DialogTitle>درخواست مشاوره با {selectedDoctor?.full_name}</DialogTitle>
                <DialogDescription>
                    علت مراجعه خود را بنویسید. این اطلاعات برای متخصص ارسال می‌شود تا درخواست شما را بررسی کند.
                </DialogDescription>
            </DialogHeader>
            <div className="grid gap-4 py-2">
                <div className="grid gap-2">
                    <Label htmlFor="concern" className="text-right">علت اصلی مراجعه <span className="text-red-500">*</span></Label>
                    <Textarea 
                        id="concern" 
                        placeholder="مثلا: اضطراب شدید، مشکلات خواب..."
                        value={formData.main_concern}
                        onChange={(e) => setFormData({...formData, main_concern: e.target.value})}
                        className="resize-none min-h-[80px]"
                    />
                </div>
                <div className="grid gap-2">
                    <Label htmlFor="history" className="text-right">سابقه مختصر (اختیاری)</Label>
                    <Input 
                        id="history" 
                        placeholder="سابقه گذشته..."
                        value={formData.history_brief}
                        onChange={(e) => setFormData({...formData, history_brief: e.target.value})}
                    />
                </div>
                <div className="grid gap-2">
                    <Label htmlFor="time" className="text-right">زمان‌های پیشنهادی (اختیاری)</Label>
                    <Input 
                        id="time" 
                        placeholder="مثلا: عصرها بعد از ساعت ۱۷"
                        value={formData.preferred_time}
                        onChange={(e) => setFormData({...formData, preferred_time: e.target.value})}
                    />
                </div>
            </div>
            <DialogFooter className="gap-2 sm:gap-0">
                <Button variant="outline" onClick={() => setSelectedDoctor(null)}>انصراف</Button>
                <Button onClick={handleSubmitRequest} disabled={isRequesting}>
                    {isRequesting ? <Loader2 className="h-4 w-4 animate-spin" /> : "ارسال درخواست"}
                </Button>
            </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* --- Expert Full Profile Modal --- */}
      <Dialog open={!!profileDoctor} onOpenChange={(o) => !o && setProfileDoctor(null)}>
        <DialogContent className="sm:max-w-lg max-h-[85vh] overflow-y-auto" dir="rtl">
          <DialogHeader className="text-right">
            <DialogTitle>اطلاعات کامل متخصص</DialogTitle>
            <DialogDescription>
              جزئیات کامل پروفایلی که متخصص در تنظیمات خود ثبت کرده است.
            </DialogDescription>
          </DialogHeader>

          {profileDoctor && (
            <div className="space-y-5 py-1">
              <div className="flex items-start gap-3 rounded-lg border p-3 bg-muted/20">
                <Avatar className="h-14 w-14 border">
                  <AvatarImage src={fixAvatarUrl(profileDoctor.avatar) || ""} alt={profileDoctor.full_name} className="object-cover" />
                  <AvatarFallback className="bg-primary/10 text-primary text-lg">
                    {profileDoctor.full_name ? profileDoctor.full_name.slice(0, 1) : "D"}
                  </AvatarFallback>
                </Avatar>
                <div className="space-y-1 min-w-0">
                  <h3 className="font-semibold text-base truncate">{profileDoctor.full_name || "متخصص"}</h3>
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Badge variant="secondary" className="font-normal">{profileDoctor.specialty || "—"}</Badge>
                    {profileDoctor.expert_profession_label && (
                      <Badge variant="outline" className="font-normal">{profileDoctor.expert_profession_label}</Badge>
                    )}
                  </div>
                </div>
              </div>

              <div className="space-y-2 text-sm">
                <div className="grid grid-cols-1 sm:grid-cols-[140px_1fr] gap-1 sm:gap-3">
                  <span className="text-muted-foreground">موقعیت مکانی</span>
                  <span>{profileDoctor.location_name ? parseLocationName(profileDoctor.location_name).label : "ثبت نشده"}</span>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-[140px_1fr] gap-1 sm:gap-3">
                  <span className="text-muted-foreground">آدرس مطب</span>
                  <span>{profileDoctor.clinic_address || "ثبت نشده"}</span>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-[140px_1fr] gap-1 sm:gap-3">
                  <span className="text-muted-foreground">پذیرش مراجعه‌کننده جدید</span>
                  <span>{profileDoctor.accepting_new_patients ? "فعال" : "غیرفعال"}</span>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-[140px_1fr] gap-1 sm:gap-3">
                  <span className="text-muted-foreground">هزینه جلسه</span>
                  <span>
                    {(() => {
                      const meetingPrice = getMeetingPriceDisplay(profileDoctor.meeting_price);
                      if (!meetingPrice) return "ثبت نشده";
                      return `${meetingPrice.full} (${meetingPrice.words})`;
                    })()}
                  </span>
                </div>
              </div>

              <div className="space-y-2">
                <h4 className="text-sm font-medium">درباره متخصص</h4>
                <div className="rounded-lg border p-3 text-sm leading-7 whitespace-pre-wrap">
                  {profileDoctor.bio || "متخصص توضیحی ثبت نکرده است."}
                </div>
              </div>
            </div>
          )}

          <DialogFooter>
            <Button variant="outline" onClick={() => setProfileDoctor(null)}>بستن</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
