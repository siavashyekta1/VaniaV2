// frontend/lib/types.ts

// --- Economy Configuration ---
export interface SupportContact {
  role?: string;
  name: string;
  phone: string;
}

export interface EconomyConfig {
  currency_name: string;
  currency_symbol: string;
  daily_free_credits: string; 
  transcription_cost_per_minute: string;
  bank_card_number?: string;
  bank_holder_name?: string;
  manual_payment_tips?: string;
  support_phone?: string;
  support_email?: string;
  support_address?: string;
  support_postal_code?: string;
  support_contacts?: SupportContact[];
}

export interface FAQItem {
  id: number;
  question: string;
  answer: string;
  category: string;
}

// --- Subscription Plans ---
export interface SubscriptionPlan {
  id: number;
  slug: string;
  name: string;
  description: string;
  price: string;
  included_credits: string;
  included_agents: string[]; // List of agent names/slugs unlocked
  included_agent_slugs?: string[];
  audience?: 'ALL' | 'VISITOR' | 'EXPERT';
  eligible_expert_professions?: string[];
  is_active: boolean;
}

// --- User & Wallet Definitions ---
export interface WalletInfo {
  id: number;
  // Balances as strings to preserve decimal precision
  balance_plan: string;    
  balance_paid: string;
  daily_free_used: string;
  
  // Plan Metadata (Flattened from UserWalletSerializer)
  active_plan_name?: string | null;
  
  updated_at: string;
}

export interface UserData {
  id: number;
  phone_number: string;
  full_name?: string;
  email?: string;
  date_joined: string;
  is_staff?: boolean;
  is_superuser?: boolean;
  role?: string; // Legacy auth responses may still return this instead of role_slug.
  role_slug?: string;  // e.g., 'expert' | 'visitor' (legacy aliases may still appear)
  role_label?: string; // e.g., 'متخصص' | 'مراجعه‌کننده'
  national_code?: string | null;
  is_expert_verified?: boolean;
  expert_profession_slug?: string | null;
  expert_profession_label?: string | null;
  has_all_expert_access?: boolean;
  expert_verification_status?: 'none' | 'pending' | 'approved' | 'rejected';
  expert_verification_message?: string | null;
  expert_verification_requested_at?: string | null;
  expert_verification_can_retry?: boolean;
  has_password?: boolean;
  // Single Wallet Object (No more roles list or primary_wallet_info)
  wallet?: WalletInfo;
}

// --- Agent Service ---

// [NEW] Demo Mode Configuration Types
export type DemoAccessMode = 'ALLOWED' | 'BLOCKED';
export type DemoLimitScope = 'SESSION' | 'DAILY' | 'TOTAL' | 'NONE';
export type DemoCanvasMode = 'HIDDEN' | 'LOCKED' | 'OPEN';

export interface DemoConfig {
  access_mode: DemoAccessMode;
  model_override?: string | null;
  message_limit_scope: DemoLimitScope;
  message_limit_count: number;
  canvas_mode: DemoCanvasMode;
  canvas_placeholder_text?: string;
}

export interface ServiceSuggestion {
  title: string;
  subtitle: string;
  prompt: string;
}

export interface AgentUIConfig {
  has_canvas: boolean;
  default_width: number;
  show_voice_input: boolean;
  mobile_view_default?: 'chat' | 'canvas';
  featured?: boolean;
  featured_label?: string;
  featured_variant?: string;
}

export interface AgentService {
  id: number;
  name: string;
  slug: string;
  description: string;
  system_prompt?: string;
  model_id?: string;
  user_guide?: string;
  supported_canvases: string[]; 
  ui_config: AgentUIConfig; 
  is_free: boolean;
  audience?: 'ALL' | 'VISITOR' | 'EXPERT';
  eligible_expert_professions?: string[];
  requires_visitor_selector?: boolean;
  is_public: boolean;
  is_active: boolean;
  is_accessible: boolean;
  
  // Dynamic Access Status (Computed by Backend)
  access_status: 'FREE' | 'OWNED' | 'LOCKED' | 'MAINTENANCE';
  is_owned: boolean;

  // [NEW] Demo Rules & Usage from Backend
  demo_config?: DemoConfig;
  current_usage?: number; // Usage count for DAILY or TOTAL scopes at page load
  
  tags: string[];               
  cost_multiplier: string; 
  suggestions: ServiceSuggestion[];
  quick_actions?: { handle: string; name: string }[];
  
  // Technical Logic
  reasoning_type?: 'NATIVE' | 'HYBRID' | 'NONE'; 
  capabilities?: string[];
  enable_reasoning: boolean;
  reasoning_effort: 'low' | 'medium' | 'high' | 'none';
}

// --- Billing & Invoicing ---
export interface BillingProduct { 
  id: number; 
  name: string; 
  description: string; 
  price: string; 
  credit_amount: string; 
  
  // Nested Plan Details (if this product is a plan activation)
  plan_details?: SubscriptionPlan; 
}

export interface Invoice {
  id: string;
  status: "PENDING" | "PAID" | "CANCELLED" | "WAITING" | "WAITING_APPROVAL";
  subtotal_amount?: string;
  tax_rate?: string;
  tax_amount?: string;
  total_amount: string;
  created_at: string;
  item_name?: string;
  item_description?: string;
  user_name?: string;
  user_phone?: string;
  transaction_ref_id?: string;
  card_number?: string | null;
  authority?: string | null;
  discount_amount?: string;
}

// --- Dynamic Forms ---
export interface FormField {
  name: string;
  label: string;
  type: "select" | "checkbox" | "text" | "number" | "email" | "textarea" | "date";
  options?: string[]; 
  required?: boolean;
  placeholder?: string;
}

export interface FormPayload {
  form_handle: string; 
  title: string;
  description?: string;
  prefill?: Record<string, any>; 
  schema: FormField[];
}
