import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"
import { APP_CONFIG } from "@/lib/config";
// 1. The Class Name Helper (Restored)
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

// 2. The Currency Formatter (Added)
export function formatExactCurrency(value: number | string | null | undefined): string {
  const amount = Number(value ?? 0);
  return `${(Number.isFinite(amount) ? amount : 0).toLocaleString(APP_CONFIG.ECONOMY.LOCALE, { maximumFractionDigits: 2 })} ${APP_CONFIG.ECONOMY.CURRENCY_SYMBOL}`;
}

export function formatCurrency(value: number | string | null | undefined): string {
  // 1. Safety & Parsing
  if (value === null || value === undefined || value === "") {
    return `۰ ${APP_CONFIG.ECONOMY.CURRENCY_SYMBOL}`;
  }

  const num = typeof value === "string" ? parseFloat(value) : value;
  
  if (isNaN(num)) {
    return `۰ ${APP_CONFIG.ECONOMY.CURRENCY_SYMBOL}`;
  }

  const absValue = Math.abs(num);
  const locale = APP_CONFIG.ECONOMY.LOCALE;
  const symbol = APP_CONFIG.ECONOMY.CURRENCY_SYMBOL;

  // 2. Large Number Formatting (Billion)
  if (absValue >= 1e9) {
    return `${(num / 1e9).toLocaleString(locale, { maximumFractionDigits: 1 })} میلیارد ${symbol}`;
  }

  // 3. Medium Number Formatting (Million)
  if (absValue >= 1e6) {
    return `${(num / 1e6).toLocaleString(locale, { maximumFractionDigits: 1 })} میلیون ${symbol}`;
  }

  // 4. Thousand Formatting
  if (absValue >= 10000) {
    return `${(num / 1e3).toLocaleString(locale, { maximumFractionDigits: 0 })} هزار ${symbol}`;
  }

  // 5. Standard Formatting (Small numbers)
  return `${num.toLocaleString(locale)} ${symbol}`;
}


export function formatCurrencyUsd(value: number | string | null | undefined): string {
  // 1. Safety & Parsing
  if (value === null || value === undefined || value === "") {
    return `$0`;
  }

  const num = typeof value === "string" ? parseFloat(value) : value;
  
  if (isNaN(num)) {
    return `$0`;
  }

  const absValue = Math.abs(num);
  const locale = APP_CONFIG.ECONOMY.LOCALE;
  const symbol = APP_CONFIG.ECONOMY.CURRENCY_SYMBOL;

  // 2. Large Number Formatting (Billion)
  if (absValue >= 1e9) {
    return `$${(num / 1e9).toLocaleString(locale, { maximumFractionDigits: 1 })}B`;
  }

  // 3. Medium Number Formatting (Million)
  if (absValue >= 1e6) {
    return `$${(num / 1e6).toLocaleString(locale, { maximumFractionDigits: 1 })}M`;
  }

  // 4. Thousand Formatting
  if (absValue >= 10000) {
    return `$${(num / 1e3).toLocaleString(locale, { maximumFractionDigits: 0 })}K`;
  }

  // 5. Standard Formatting (Small numbers)
  return `$${num.toLocaleString(locale)}`;
}



export function formatWeight(value: number | string | null | undefined): string {
  // 1. Safety & Parsing
  if (value === null || value === undefined || value === "") {
    return `0kg`;
  }

  const num = typeof value === "string" ? parseFloat(value) : value;
  
  if (isNaN(num)) {
    return `0kg`;
  }

  const absValue = Math.abs(num);
  const locale = APP_CONFIG.ECONOMY.LOCALE;
  const symbol = APP_CONFIG.ECONOMY.CURRENCY_SYMBOL;

  // 2. Large Number Formatting (Billion)
  if (absValue >= 1e9) {
    return `${(num / 1e9).toLocaleString(locale, { maximumFractionDigits: 1 })}BT`;
  }

  // 3. Medium Number Formatting (Million)
  if (absValue >= 1e6) {
    return `${(num / 1e6).toLocaleString(locale, { maximumFractionDigits: 1 })}MT`;
  }

  // 4. Thousand Formatting
  if (absValue >= 10000) {
    return `${(num / 1e3).toLocaleString(locale, { maximumFractionDigits: 1 })}T`;
  }

  // 5. Standard Formatting (Small numbers)
  return `${num.toLocaleString(locale)}kg`;
}


export function fixAvatarUrl(url: string | null | undefined): string {
  if (!url) return "";
  
  // Check for local IP/localhost on typical MinIO port 9000
  if (url.includes("https://127.0.0.1:9000") || url.includes("https://localhost:9000")) {
    return url.replace("https://", "http://");
  }
  
  return url;
}
