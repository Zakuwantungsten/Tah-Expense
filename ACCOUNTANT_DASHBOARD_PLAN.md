# Tahmeed Expense System — Accountant Dashboard
## Full Implementation Plan & UI Specification

> **Status:** Planning / Pre-implementation
> **Author:** System Design Session — June 10, 2026
> **Stack:** PySide6 · Motor (async MongoDB) · Python 3.11+

---

## Table of Contents

1. [System Understanding](#1-system-understanding)
2. [Design System](#2-design-system)
3. [Layout Architecture](#3-layout-architecture)
4. [Sidebar Navigation Specification](#4-sidebar-navigation-specification)
5. [Section-by-Section UI Specs](#5-section-by-section-ui-specs)
   - 5.1 Overview Dashboard
   - 5.2 Verification Inbox
   - 5.3 Master Expenses Table
   - 5.4 Category Sub-Tables
   - 5.5 Diesel Stations
   - 5.6 Separate Expenses
   - 5.7 Reconciliation (SM Burhani & RahnTech)
   - 5.8 Management Panel
6. [Data Flow & Verification Gate](#6-data-flow--verification-gate)
7. [MongoDB Collections (New)](#7-mongodb-collections-new)
8. [Component Library](#8-component-library)
9. [Implementation Phases](#9-implementation-phases)

---

## 1. System Understanding

### 1.1 What the Accountant Manages

The accountant is the **central financial authority** of the Tahmeed fleet expense system. Their responsibilities span four distinct domains:

| Domain | Data Source | Structure |
|--------|------------|-----------|
| **Cashier Data** (verified) | Cashiers submit → accountant approves | Master table + category sub-tables |
| **Diesel Tracking** | Accountant enters from supplier invoices | Per-station tables (Infinity, Lake, GBP) |
| **Separate Expenses** | Manual entry or imported from external systems | Independent tables per expense type |
| **Reconciliation** | Invoices from bonding/fee companies | Per-station reconciliation sheets |

### 1.2 The Verification Gate

Cashier entries are **NOT** immediately live. The pipeline is:

```
Cashier submits entry
        │
        ▼
  [PENDING QUEUE]  ← accountant sees this in Verify Inbox
        │
  Accountant reviews
  (can edit category, flag, reject)
        │
        ▼
  Accountant APPROVES
        │
        ├──► Master Expenses Table  (all verified transactions)
        └──► Category Sub-Table     (filtered by category)
```

Rejected entries are returned to the cashier with a note. Approved entries are immutable (accountant can only annotate, not delete).

### 1.3 Real Data Sources Discovered

From examining actual Excel files used in production:

**External Imports (data comes FROM outside systems):**
| Sheet / Expense | External System | Format |
|----------------|----------------|--------|
| Toll Plaza | Zambia NRFA eToll / Dot Com Zambia | CSV/Excel download |
| Parking Congo | Congo Transporter Ledger portal | Excel export |
| Zambia Parking | Zambia prepaid account statement | Excel per week |
| RahnTech | RahnTech trip device system | Auto feed / Excel |
| SM Burhani Bonds | SM Burhani bonding company | Excel per schedule |

**Manual Entry (accountant types directly):**
| Sheet / Expense | Columns |
|----------------|---------|
| Congo Expenses | S/No, Date, LPO No, Truck No, Description, Amount, Approved By |
| Ahmed Kimvi (Klesa) | Date-stamped sheets: Date, Truck No, Particulars, Amount, Advance/Balance |
| Harrison Expenses | S/No, Date, Truck No, Trailer No, Description, USD, Kwacha |

**Cashier-fed Categories (verified then routed):**
Mileage, LATRA, C28, C40, Carbon & Permit, Council Kapiri, Council Tunduma, Nakonde Council, Return & Weighbridge, Parking Petroda, Backload Facilitation, Rope & Sealing, Radiation Taxes, Health Fee, Parking Halmashauri Tunduma, Diesel Cash

**Diesel Invoice Stations (accountant-entered from supplier invoices):**
Infinity Diesel, Lake Zambia, Lake Tunduma, GBP Diesel

### 1.4 Reconciliation Clarified

Reconciliation is **not a single table** — it is a **grouping heading** for bonding/fee companies that send periodic invoices per border station. The accountant tracks how much has been charged per entry and reconciles what is owed.

**SM Burhani** is the primary reconciliation entity, with three border-post sub-sheets:
- **Nakonde** (Tanzania–Zambia border)
- **Kasumbalesa** (Zambia–DRC border)
- **Sakania** (Zambia–DRC border)

Columns: SR. NO · SM REF NO · PRN NUMBER · ENTRY REG NO · T1 NO · T1 DATE · IMPORTER · CONSIGNMENT · TRUCK & TRAILER DETAILS · CHARGE

Accountant must be able to **add, remove, and update stations** under any reconciliation entity.

---

## 2. Design System

### 2.1 Color Palette

Inspired by Intuit/QuickBooks enterprise design system and professional accounting desktop standards.

```
PRIMARY COLORS
──────────────────────────────────────────────────────────────
  Sidebar Navy        #1B2B4B    Dark background for navigation
  Primary Blue        #0077C5    Primary action, selected state, links
  Active Blue Light   #E8F4FD    Sidebar active item background
  Header White        #FFFFFF    Top header bar

BACKGROUND & SURFACE
──────────────────────────────────────────────────────────────
  App Background      #F4F6F8    Main content area background
  Card Surface        #FFFFFF    All data panels and cards
  Card Border         #E5E7EB    Subtle card outlines
  Table Row Alt       #F9FAFB    Alternating table rows
  Table Row Hover     #EFF6FF    Row hover state

STATUS COLORS
──────────────────────────────────────────────────────────────
  Success Green       #16A34A    Verified, received receipts
  Success Light       #DCFCE7    Success badge background
  Warning Amber       #D97706    Pending, review-needed
  Warning Light       #FEF3C7    Pending badge background
  Danger Red          #DC2626    Missing receipt, rejected
  Danger Light        #FEE2E2    Danger badge background
  Info Blue           #0284C7    Informational states
  Info Light          #E0F2FE    Info badge background

TEXT
──────────────────────────────────────────────────────────────
  Text Primary        #111827    Headings, primary labels
  Text Secondary      #6B7280    Subheadings, helper text
  Text Muted          #9CA3AF    Placeholders, disabled
  Text On-Dark        #F9FAFB    Text on navy sidebar
  Text Sidebar Muted  #94A3B8    Inactive sidebar labels
  Text Link           #0077C5    Clickable links
```

### 2.2 Typography

```
FONT STACK
  Primary:    "Segoe UI", "Inter", -apple-system, sans-serif
  Monospace:  "Cascadia Code", "Consolas", monospace  (amounts, IDs)

SCALE
  Display     24px / weight 700   Page titles
  Heading     18px / weight 600   Section headers
  Subheading  14px / weight 600   Card titles, column headers
  Body        13px / weight 400   Default text, table cells
  Caption     11px / weight 400   Timestamps, helper text
  Mono        13px / weight 500   TZS/USD amounts, serial numbers
```

### 2.3 Spacing & Sizing

```
  Base unit:         4px
  Sidebar width:     220px (collapsed: 56px)
  Header height:     52px
  Card padding:      16px
  Section gap:       20px
  Table row height:  36px (compact: 30px, comfortable: 44px)
  Border radius:     6px (cards), 4px (badges), 20px (pills)
  Shadow:            0 1px 3px rgba(0,0,0,0.08), 0 1px 2px rgba(0,0,0,0.04)
```

### 2.4 Icons

Use **qtawesome** (`pip install qtawesome`) with the **Material Design** icon set (`mdi.*`):

| UI Location | Icon Key | Meaning |
|-------------|----------|---------|
| Overview | `mdi.view-dashboard-outline` | Dashboard home |
| Verify Inbox | `mdi.inbox-arrow-down` | Pending approvals |
| Master Expenses | `mdi.table-large` | Full ledger |
| Categories | `mdi.tag-multiple-outline` | Category list |
| Mileage | `mdi.road-variant` | Distance/routes |
| LATRA | `mdi.card-account-details-outline` | License authority |
| C28 / C40 | `mdi.file-document-outline` | Customs documents |
| Diesel | `mdi.gas-station-outline` | Fuel stations |
| Separate Expenses | `mdi.cash-multiple` | Independent expenses |
| Toll Plaza | `mdi.boom-gate-outline` | Toll barriers |
| Parking | `mdi.parking` | Parking lots |
| Congo | `mdi.map-marker-outline` | Congo location |
| Zambia | `mdi.map-outline` | Zambia location |
| Reconciliation | `mdi.scale-balance` | Balance/reconcile |
| SM Burhani | `mdi.bank-outline` | Bonding company |
| RahnTech | `mdi.devices` | Device system |
| Management | `mdi.cog-outline` | Settings |
| Import | `mdi.upload-outline` | File upload |
| Export | `mdi.download-outline` | File export |
| Search | `mdi.magnify` | Search |
| Filter | `mdi.filter-variant` | Filter panel |
| Add | `mdi.plus-circle-outline` | New record |
| Edit | `mdi.pencil-outline` | Edit record |
| Delete | `mdi.trash-can-outline` | Remove record |
| Approve | `mdi.check-circle-outline` | Approve action |
| Reject | `mdi.close-circle-outline` | Reject action |
| Collapse | `mdi.chevron-left` | Sidebar collapse |
| Expand | `mdi.chevron-right` | Expand section |
| Notification | `mdi.bell-outline` | Alert badge |
| Logout | `mdi.logout-variant` | Sign out |
| Amount/Money | `mdi.currency-usd` | Financial amount |
| Receipt | `mdi.receipt-outline` | Receipt document |
| Truck | `mdi.truck-outline` | Vehicle reference |
| Calendar | `mdi.calendar-range` | Date picker |
| Year | `mdi.calendar-today` | Year selector |
| Refresh | `mdi.refresh` | Reload data |

---

## 3. Layout Architecture

### 3.1 Top-Level Window Structure

```
┌─────────────────────────────────────────────────────────────────────┐
│  TOP HEADER BAR                                          height: 52px│
│  [≡ Logo]  Tahmeed Expense System         [🔔 3] [AA] Logout       │
├──────────────┬──────────────────────────────────────────────────────┤
│              │                                                       │
│   SIDEBAR    │              MAIN CONTENT AREA                       │
│   width:220  │              flex: 1, overflow-y: scroll             │
│              │                                                       │
│  Navigation  │   ┌─ BREADCRUMB BAR (28px) ──────────────────────┐  │
│  sections    │   │  Accountant > Master Expenses > January 2025  │  │
│  with icons  │   └───────────────────────────────────────────────┘  │
│  + labels    │                                                       │
│              │   ┌─ PAGE CONTENT ────────────────────────────────┐  │
│              │   │                                               │  │
│  ──────────  │   │  (Changes per sidebar selection)             │  │
│  COLLAPSE    │   │                                               │  │
│  [◀]         │   └───────────────────────────────────────────────┘  │
└──────────────┴──────────────────────────────────────────────────────┘
│  BOTTOM STATUS BAR                                       height: 24px│
│  Connected · MongoDB Atlas     Last sync: 14:32:07      v1.0.0      │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.2 Header Bar Detail

```
┌─────────────────────────────────────────────────────────────────────┐
│ [≡]  [T] TAHMEED               [🔍 Search...        ] [🔔] [AA ▾] │
└─────────────────────────────────────────────────────────────────────┘
```

- **[≡]** — Hamburger to collapse/expand sidebar
- **[T]** — Company logo (logo.png from project root)
- **Global Search** — Cross-module quick search (truck number, description, amount)
- **[🔔]** — Bell icon with red badge showing pending verifications count
- **[AA ▾]** — User avatar (initials), dropdown: Profile · Change Password · Logout

### 3.3 Sidebar Collapsed State (56px)

When collapsed, sidebar shows only icons with tooltips on hover. Section group labels hide. The active section icon gets a blue left-border indicator.

### 3.4 Bottom Status Bar

```
● Connected · MongoDB Atlas Cloud    |    Last refresh: 14:32:07    |    FY 2025    v1.0.0
```

Clicking "FY 2025" opens a year-selector dropdown to change the active financial year across all views.

---

## 4. Sidebar Navigation Specification

```
SIDEBAR (width: 220px, bg: #1B2B4B)
══════════════════════════════════

  ┌──────────────────────────────┐
  │  [≡]  TAHMEED EXPENSE        │  ← Header area (52px), logo + app name
  └──────────────────────────────┘

  ● OVERVIEW                          mdi.view-dashboard-outline
  ─────────────────────────────────
  CASHIER FLOW
    ○ Verify            [●12]         mdi.inbox-arrow-down       ← badge = pending count
    ○ Master Expenses                 mdi.table-large

  ─────────────────────────────────
  CATEGORIES
    ○ Mileage             ▸           mdi.road-variant           ← expandable
        ├ All Routes
        ├ Dar – Congo
        ├ [+ Add Route]
    ○ LATRA                           mdi.card-account-details-outline
    ○ C28                             mdi.file-document-outline
    ○ C40                             mdi.file-document-outline
    ○ Carbon & Permit                 mdi.leaf-circle-outline
    ○ Diesel Cash                     mdi.gas-station-outline
    ○ Council Fees        ▸           mdi.city-variant-outline   ← expandable
        ├ Kapiri
        ├ Tunduma
        ├ Nakonde
        ├ [+ Add Council]
    ○ Return & Weighbridge            mdi.weight
    ○ Parking Petroda                 mdi.parking
    ○ Backload Facilitation           mdi.truck-delivery-outline
    ○ Rope & Sealing                  mdi.rope
    ○ Radiation Taxes                 mdi.radioactive-circle-outline
    ○ Health Fee                      mdi.hospital-box-outline
    ○ Halmashauri Parking             mdi.parking
    ○ [+ Add Category]                mdi.plus-circle-outline    ← add new

  ─────────────────────────────────
  DIESEL STATIONS
    ○ Infinity                        mdi.gas-station
    ○ Lake Zambia                     mdi.water-pump
    ○ Lake Tunduma                    mdi.water-pump
    ○ GBP Diesel                      mdi.fuel
    ○ [+ Add Station]                 mdi.plus-circle-outline

  ─────────────────────────────────
  SEPARATE EXPENSES
    ○ Toll Plaza                      mdi.boom-gate-outline
    ○ Parking Congo                   mdi.parking
    ○ Congo Expenses                  mdi.map-marker-outline
    ○ Ahmed Kimvi (Klesa)             mdi.account-cash-outline
    ○ Zambia Parking                  mdi.map-outline
    ○ Harrison Expenses               mdi.account-tie-outline
    ○ Afritrack                       mdi.satellite-uplink
    ○ Third Party Covers              mdi.shield-account-outline
    ○ COMESA Covers                   mdi.file-certificate-outline

  ─────────────────────────────────
  RECONCILIATION
    ○ SM Burhani          ▸           mdi.scale-balance          ← expandable
        ├ Nakonde
        ├ Kasumbalesa
        ├ Sakania
        ├ [+ Add Station]
    ○ RahnTech                        mdi.devices
    ○ [+ Add Reconciliation]          mdi.plus-circle-outline

  ─────────────────────────────────
  MANAGE
    ○ Categories                      mdi.tag-edit-outline
    ○ Diesel Stations                 mdi.gas-station-outline
    ○ Recon. Stations                 mdi.office-building-outline
    ○ Separate Expenses               mdi.view-list-outline

  ─────────────────────────────────
  [◀ Collapse]
```

**Sidebar behavior:**
- Active item: left border `3px solid #0077C5`, bg `#253A5C`, text `#FFFFFF`
- Hover item: bg `#253A5C`
- Section header labels: uppercase, 10px, `#94A3B8`, letter-spacing 0.8px
- Expandable items: chevron rotates on expand/collapse, sub-items indented 16px
- Badge (●12): red pill `#DC2626`, 18px height, white text, positioned right

---

## 5. Section-by-Section UI Specs

---

### 5.1 Overview Dashboard

**Purpose:** At-a-glance financial health for the current financial year.

```
┌─ OVERVIEW ─────────────────────────────────────────────────────────┐
│  Good morning, Accountant.  Wednesday, 10 June 2026    FY 2025  ▾  │
└─────────────────────────────────────────────────────────────────────┘

┌── KPI CARDS (4 across) ────────────────────────────────────────────┐
│                                                                     │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌───────────┐ │
│  │ 🔔 PENDING   │ │ 📊 MASTER    │ │ ✅ VERIFIED  │ │ 💰 TOTAL  │ │
│  │ VERIFICATION │ │ ENTRIES      │ │  THIS MONTH  │ │  TZS/USD  │ │
│  │              │ │              │ │              │ │           │ │
│  │     12       │ │    4,821     │ │     387      │ │ 847.3M    │ │
│  │  entries     │ │   YTD total  │ │  of 399      │ │   TZS     │ │
│  │  awaiting    │ │              │ │              │ │  $14,210  │ │
│  └──────────────┘ └──────────────┘ └──────────────┘ └───────────┘ │
└─────────────────────────────────────────────────────────────────────┘

┌── MONTHLY EXPENSE TREND (bar chart) ──────┐  ┌── BY CATEGORY ────┐
│                                            │  │  Pie chart:        │
│  Jan  Feb  Mar  Apr  May  Jun              │  │  Mileage 28%       │
│  ████ ████ ████ ████ ████ ░░░             │  │  LATRA 18%         │
│                                            │  │  Diesel 15%        │
│  — TZS  — USD                             │  │  Council 12%       │
└────────────────────────────────────────────┘  │  Other 27%         │
                                                └────────────────────┘

┌── RECEIPT STATUS BREAKDOWN ───────────────┐  ┌── RECENT ACTIVITY ┐
│  ✅ Received   3,241  ██████████████ 67%  │  │  ─ Jun 9 ──────── │
│  ⏳ Pending      982  ███████        20%  │  │  T588 DRE LATRA    │
│  ❌ Missing      598  ████           13%  │  │  T760 DNH Mileage  │
└────────────────────────────────────────────┘  │  ─ Jun 8 ──────── │
                                                │  [View All →]      │
                                                └────────────────────┘

┌── QUICK ACTIONS ───────────────────────────────────────────────────┐
│  [✅ Go to Verify Inbox]  [📊 Master Table]  [⬇ Export Report]   │
└─────────────────────────────────────────────────────────────────────┘
```

---

### 5.2 Verification Inbox

**Purpose:** Review, edit, approve, or reject cashier-submitted transactions before they enter the ledger.

```
┌─ VERIFY INBOX ─────────────────────────────────── 12 pending ──────┐
│                                                                      │
│  [🔍 Search description...]  [Truck ▾] [Cashier ▾] [Date ▾]        │
│  [✅ Approve Selected]  [❌ Reject Selected]                         │
│                                                                      │
│  ☑  S/NO   DATE        CASHIER      DESCRIPTION          TRUCK       │
│  ─────────────────────────────────────────────────────────────────  │
│  ☑  001    09 Jun 26   Amina J.     LATRA                T588 DRE    │
│  │         Category: LATRA [✓ auto 92%]    TZS: 130,000  Pending  │
│  ──────────────────────────────────────────────────────────────── │
│  ☑  002    09 Jun 26   Amina J.     COUNCIL TUNDUMA       T164 DZY   │
│  │         Category: ⚠ Unmatched [review]  TZS:  15,000  Pending  │
│  ──────────────────────────────────────────────────────────────── │
│  ☐  003    08 Jun 26   John M.      TOLL ROAD             T712 DXY   │
│             Category: Return & Weighbridge  TZS:  27,600  Pending  │
│                                                                      │
│  [Show 25 ▾]       Page 1 of 1        [← Prev]  [Next →]           │
└──────────────────────────────────────────────────────────────────────┘
```

**Expanded Row (click to expand):**
```
┌─ Transaction Detail ────────────────────────────────────────────────┐
│  Date: 09 Jun 2026          Cashier: Amina Juma                     │
│  Truck: T588 DRE            Approver (APR BY): Sarahani             │
│  LPO Ref: —                 DO No: —                                │
│  Description: LATRA                                                  │
│  Item: —       Memo: —      Ownership: —                            │
│  Amount: TZS 130,000        USD: —                                  │
│  Receipt: ⏳ Pending         Notes Flag: ☐                           │
│                                                                      │
│  Category: [ LATRA               ▾ ]  ← accountant can change      │
│  Notes for cashier: [________________________]                       │
│                                                                      │
│  [✅ Approve]   [❌ Reject & Return]   [💾 Save & Next]             │
└──────────────────────────────────────────────────────────────────────┘
```

**Workflow:**
- On **Approve**: `verified = True`, `verified_by = accountant._id`, `verified_at = now()`. Transaction is written to Master Expenses and the matching category sub-table simultaneously.
- On **Reject**: `verified = False`, `rejection_reason = note`. Cashier sees it back in their view with the note.
- **Bulk Approve**: Checkbox + "Approve Selected" button — only auto-matched (confidence ≥ threshold) can be bulk-approved; unmatched require individual review.

---

### 5.3 Master Expenses Table

**Purpose:** The complete yearly ledger of all verified transactions. Read-mostly. Mirrors the original MASTER EXPENSES Excel sheet exactly.

**Columns (matching real Excel headers):**
`S/NO · DATE · MONTH · DESCRIPTION · TRUCK NO · LPO NOS · DO NO · MEMO · NOTES · TZS · USD · RECEIPT STATUS · OWNERSHIP`

```
┌─ MASTER EXPENSES ─────────────────────────── FY 2025 ▾ ─── 4,821 records ──┐
│                                                                               │
│  [🔍 Search...]  [Month ▾]  [Truck ▾]  [Category ▾]  [Receipt ▾]            │
│                             [⬇ Export Excel]  [⬇ Export CSV]                 │
│                                                                               │
│  S/NO  DATE        MONTH     DESCRIPTION           TRUCK       LPO     DO    │
│  ────  ──────────  ────────  ────────────────────  ──────────  ──────  ────  │
│  1     31 Dec 24   Dec 24    WEIGHBRIDGE           T525 DPN    —       —     │
│  2     31 Dec 24   Dec 24    RETURN TUNDUMA        T164 DZY    —       —     │
│  3     31 Dec 24   Dec 24    RETURN MBEYA          T134 DZY    —       —     │
│  ...                                                                          │
│                                                                               │
│  ← continued columns →  MEMO  NOTES  TZS          USD    RECEIPT   OWNERSHIP│
│                          —     —     (49,835)      —      NO RCPT   —        │
│                          —     —     (100,000)     —      NO RCPT   —        │
│                                                                               │
│  TOTAL (filtered):               TZS  847,341,200          USD  14,210.50    │
│                                                                               │
│  [Show 50 ▾]          Page 1 of 97          [← Prev]  [Next →]              │
└───────────────────────────────────────────────────────────────────────────────┘
```

**Features:**
- **Year Filter**: Top-right FY selector. Defaults to current year, persists across session.
- **Month Tabs**: Optional quick filter tabs: All · Jan · Feb · Mar ... Dec (shows monthly totals in tabs)
- **Column Resizing**: All columns drag-resizable.
- **Sort**: Click any column header to sort ascending/descending.
- **Frozen columns**: S/NO, DATE, DESCRIPTION always visible; others scroll horizontally.
- **TZS amounts**: Shown in red for negative (expenses), green for positive (returns/refunds).
- **Receipt badge**: Color-coded pill — `Received` (green) · `Pending` (amber) · `No Receipt` (red).
- **Row right-click**: View Full Detail · Copy Row · Export This Row.
- **Footer totals**: TZS total and USD total for the current filtered view.

---

### 5.4 Category Sub-Tables

**Purpose:** Each category gets its own view showing only the verified transactions belonging to it. The accountant can manage which categories exist.

#### 5.4.1 Standard Category View (e.g., LATRA, Health Fee, Parking)

Most categories share the same column schema:

`S/NO · DATE · DESCRIPTION · TRUCK NO · TZS · USD · RECEIPT STATUS`

```
┌─ LATRA ──────────────────────────────────── FY 2025 ─── 1,203 records ──┐
│                                                                            │
│  [🔍 Search truck...]  [Month ▾]  [Receipt ▾]     [⬇ Export]             │
│                                                                            │
│  S/NO   DATE        DESCRIPTION   TRUCK NO    TZS          RECEIPT        │
│  ─────  ──────────  ────────────  ──────────  ───────────  ─────────────  │
│  407    04 Jan 24   LATRA         T619 DRH    (130,000)    ⏳ No Receipt  │
│  409    04 Jan 24   LATRA         T655 DRU    (130,000)    ⏳ No Receipt  │
│  410    04 Jan 24   LATRA         T337 DRP    (130,000)    ✅ Receipt     │
│                                                                            │
│  MONTHLY TOTAL:    TZS  (16,900,000)          USD  —                      │
└────────────────────────────────────────────────────────────────────────────┘
```

#### 5.4.2 Mileage (Special — with Route Sub-breakdown)

```
┌─ MILEAGE ──────────────────────────────────────────────────────────────────┐
│                                                                              │
│  Route:  [All Routes ▾]    [+ Add Route]    Month: [All ▾]   [⬇ Export]   │
│                                                                              │
│  ROUTE SUMMARY CARDS                                                        │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐            │
│  │ Dar – Congo     │  │ Return Tunduma  │  │ Return Mbeya    │            │
│  │  TZS 42,800,000 │  │  TZS 8,100,000  │  │  TZS 6,500,000  │            │
│  │  [View Table]   │  │  [View Table]   │  │  [View Table]   │            │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘            │
│                                                                              │
│  ALL ROUTES TABLE                                                           │
│  S/NO   DATE        ROUTE (Description)    TRUCK NO    TZS          RCPT   │
│  ─────  ──────────  ──────────────────────  ──────────  ───────────  ─────  │
│  2      31 Dec 24   RETURN TUNDUMA          T164 DZY    (100,000)    —      │
│  3      31 Dec 24   RETURN MBEYA            T134 DZY    (100,000)    —      │
│                                                                              │
│  TOTAL:   TZS  (847,300,000)                                                │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Mileage route management:** "Add Route" opens a dialog to name a new route. Routes appear as cards + filter dropdown. The accountant can rename or archive routes.

#### 5.4.3 C28 and C40 (Customs Documents — different column schema)

These have unique columns: `S/N · TRAILERS · AMOUNT · CONTROL NO · DATE`

```
┌─ C28 ──────────────────────────────────────────────────────────────────────┐
│                                                                              │
│  [🔍 Search trailer/control no...]  [Month ▾]       [⬇ Export]             │
│                                                                              │
│  S/N   TRAILERS     AMOUNT (TZS)      CONTROL NO          DATE              │
│  ─────  ───────────  ────────────────  ──────────────────  ──────────────   │
│  1      T464 DNH     512,652           998354377820        14 Feb 2025       │
│  2      T465 DNH     514,160           998354393637        21 Feb 2025       │
│  3      T463 DNH     514,160           998354393634        21 Feb 2025       │
│                                                                              │
│  TOTAL:   TZS  2,841,200                                                    │
└──────────────────────────────────────────────────────────────────────────────┘
```

> Note: C28 and C40 are customs clearance documents. The cashier enters them; after verification they appear here. **No manual entry** from accountant side — purely a verified cashier view.

#### 5.4.4 Diesel Cash (Cashier-fed diesel category)

Columns: `S/NO · DATE · DESCRIPTION · TRUCK NO · MEMO · NOTES · TZS · USD · RECEIPT STATUS · APR BY`

Mirrors DIESEL CASH sheet in Master Excel. Data flows in from cashier entries tagged with "Diesel Cash" category.

#### 5.4.5 Council Fees (Three-sub-council grouping)

Shown with tab strip or expandable sub-sections: **Kapiri** · **Tunduma** · **Nakonde** · **All Combined**

Each sub-council: `S/NO · DATE · DESCRIPTION · TRUCK NO · TZS · USD · RECEIPT STATUS`

---

### 5.5 Diesel Stations

**Purpose:** Track fuel purchases from named supplier stations. Accountant enters from invoices. NOT from cashier flow.

#### Diesel Infinity / Lake Zambia / Lake Tunduma / GBP Diesel

```
┌─ DIESEL — INFINITY ────────────────────── FY 2025 ─── 1,842 records ──┐
│                                                                           │
│  [+ New Entry]  [⬆ Import Excel]  [🔍 Search...]  [Month ▾]  [⬇ Export] │
│                                                                           │
│  S/NO  DATE        LPO NO   DO/SDO    STATION       DESTINATION  TRUCK   │
│  ───   ──────────  ───────  ────────  ────────────  ───────────  ──────  │
│  1     29 Dec 24   2255     3308      INFINITY      KOLWEZI      T526 DRF│
│  2     30 Dec 24   2260     3098      INFINITY      DAR          T525 DPN│
│                                                                           │
│  ← cont → LTRS    PRICE/LTR    TOTAL (TZS)    REMARK                    │
│            450     2,671        1,201,950       —                         │
│            400     2,806        1,122,400       1st–15th Jan              │
│                                                                           │
│  TOTAL:   Litres  84,200        TZS  234,820,000                          │
└───────────────────────────────────────────────────────────────────────────┘
```

**Entry Form (New Entry dialog):**
```
  Date *          [ 29 Dec 2024   📅 ]
  LPO No.         [ 2255          ]
  DO / SDO No.    [ 3308          ]
  Station         [ INFINITY      ] ← pre-filled from which station view
  Client Name     [ TAHMEED       ] ← auto-filled
  Destination     [ KOLWEZI       ]
  Truck No.       [ T526 DRF  🚛  ]
  Litres          [ 450           ]
  Price per Litre [ 2,671         ]
  Total Amount    [ 1,201,950 TZS ] ← auto-calculated
  Remark          [ key           ]
  
  [ Save ]   [ Save & Add Another ]   [ Cancel ]
```

**Lake Zambia extra column:** `Lake US $` (same total in USD).

---

### 5.6 Separate Expenses

#### 5.6.1 Toll Plaza (KW)

**Data source: IMPORTED from Zambia NRFA eToll / Dot Com Zambia (Excel/CSV download)**

```
┌─ TOLL PLAZA ──────────────────────────────────────────────────────────────┐
│                                                                              │
│  [⬆ Import from Dot Com Zambia]  [🔍 Search...]  [Plaza ▾]  [⬇ Export]    │
│                                                                              │
│  TOLL DATE          TOLL PLAZA        VEHICLE REG   CLASS         TENDER   │
│  ─────────────────  ────────────────  ────────────  ────────────  ───────  │
│  01 Jan 25 05:59    KAFULAFUTA        T154 EGJ      Heavy 4axl+   ZMW 250  │
│  01 Jan 25 06:08    GEORGE KUNDA S.C  T793 DNH      Heavy 4axl+   ZMW 250  │
│                                                                              │
│  ← cont →   RECEIPT NO        DEVICE      LANE   CASHIER                  │
│              T3005220250...    NRFA0000    1      Benny Gondwe              │
│                                                                              │
│  Import notes: Paste the Excel downloaded from Dot Com Zambia portal.      │
│  Auto-detects columns by header name. Duplicate detection by Receipt No.   │
│  TOTAL:  ZMW  148,500    Records this period: 594                           │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Import dialog:**
```
  ┌─ Import Toll Plaza Data ───────────────────────────────┐
  │                                                         │
  │  Drag & drop or  [Browse File]  (.xlsx or .csv)        │
  │                                                         │
  │  Period detected: Jan 2025 – Jun 2026                  │
  │  New records: 147     Duplicates (skipped): 3          │
  │                                                         │
  │  [ Preview Table (first 10) ]                          │
  │                                                         │
  │  [ ✅ Import 147 Records ]   [ Cancel ]                │
  └─────────────────────────────────────────────────────────┘
```

#### 5.6.2 Parking Congo

**Data source: IMPORTED from Congo Transporter Ledger portal**

```
Columns: SN · TRANSACTION DATE · TYPE · SERIAL · VEHICLE # · AMOUNT · BALANCE · GATE IN · GATE OUT
```

Same import flow as Toll Plaza. Account is "TRA0039 — Tahmeed Coach Tz Ltd". Duplicate detection by Serial number.

#### 5.6.3 Congo Expenses (Manual Entry)

```
┌─ CONGO EXPENSES ──────────────────────────────────────────────────────────┐
│                                                                              │
│  [+ New Entry]  [🔍 Search...]  [Month ▾]  [Truck ▾]         [⬇ Export]   │
│                                                                              │
│  S/NO   DATE        LPO NO   TRUCK NO    DESCRIPTION              AMOUNT   │
│  ─────  ──────────  ───────  ──────────  ────────────────────────  ──────  │
│  1      01 Jan 25   C001     T700 DXY    Seal Facilitation         $10      │
│  2      01 Jan 25   C001     T696 DXY    Seal Facilitation         $10      │
│  3      01 Jan 25   C001     T603 EDD    Overstay                  $50      │
│                                                                              │
│  TOTAL:  USD  12,840.00      Records: 847                                   │
└──────────────────────────────────────────────────────────────────────────────┘
```

Entry form: `Date · LPO No · Truck No · Description · Amount (USD) · Approved By`

#### 5.6.4 Ahmed Kimvi / Klesa (Advance Sheet System)

This is unique — each visit/trip is a **separate dated sheet** with a cash advance at the top and itemized expenses below, tracking the running balance.

```
┌─ AHMED KIMVI (KLESA) ─────────────────────────────────────────────────────┐
│                                                                              │
│  VISIT SHEETS:  [◀]  Sheet 65: 02 Jun 2026  [▶]   [+ New Sheet]           │
│                 All Sheets ▾                        [⬇ Export All]         │
│                                                                              │
│  ┌─ Sheet Summary ────────────────────────────────────────────────────┐    │
│  │  Date: 02 Jun 2026    Cash Advance: USD (1,500)    Balance: USD 43 │    │
│  └────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  S/NO   DATE        TRUCK NO    PARTICULARS                     AMOUNT     │
│  ─────  ──────────  ──────────  ──────────────────────────────  ─────────  │
│  —      02 Jun 26   —           Cash Advance Payment            (1,500)    │
│  1      02 Jun 26   T587 DTB    Entry Card Renewal               20         │
│  2      02 Jun 26   T585 DRE    Occ Facilitation                 20         │
│  3      02 Jun 26   T208 EHE    Occ Facilitation                 20         │
│                                                                              │
│  Running Balance: USD 1,457                                                 │
└──────────────────────────────────────────────────────────────────────────────┘
```

#### 5.6.5 Zambia Parking

**Data source: IMPORTED from Tahmeed prepaid account weekly statement**

```
Columns: DATE · TYPE · PLATE NUM. · TICKET NO. · DEBIT · CREDIT · BALANCE · HEADING TO
```

Opening balance row + transaction rows per truck. Import per week (Excel file). Duplicate detection by Ticket No.

```
  Total records: 2,841   Current Balance: ZMW 18,650   Last Statement: Week 10 2026
```

#### 5.6.6 Harrison Expenses (Manual Entry)

```
Columns: S/No · Date · Truck No · Trailer No · Description · USD · Kwacha
```

Same entry form pattern as Congo Expenses. Both USD and Kwacha columns.

#### 5.6.7 Afritrack

Placeholder view for future tracking system integration. Shows "Import from Afritrack" button with expected columns TBD.

#### 5.6.8 Third Party Covers / COMESA Covers

Both are placeholder views with standard table + import button. Columns TBD when data is provided.

---

### 5.7 Reconciliation

#### SM Burhani Bonds

Each border station has its own reconciliation sheet of bonds issued by SM Burhani. Accountant can add stations.

```
┌─ RECONCILIATION — SM BURHANI ─────────────────────────────────────────────┐
│                                                                              │
│  Stations:  [● Nakonde]  [○ Kasumbalesa]  [○ Sakania]   [+ Add Station]   │
│                                                                              │
│  Schedule: [ 01 May 2026 – 15 May 2026 ▾ ]   [⬆ Import Schedule]          │
│                                                                              │
│  SR NO  SM REF NO   PRN NUMBER      ENTRY REG  T1 NO   T1 DATE    CHARGE  │
│  ─────  ──────────  ──────────────  ─────────  ──────  ─────────  ──────  │
│  1      SM0289TR    97266854416..   S78711     80276   03 May 26   $70     │
│  2      SM0290TR    97266854416..   S78719     80291   03 May 26   $70     │
│                                                                              │
│  ← cont →  IMPORTER               CONSIGNMENT    TRUCK & TRAILER           │
│             TENGYUAN COBALT        SULPHUR        T124DYY / T966DYY         │
│                                                                              │
│  SCHEDULE TOTAL:   $4,270.00      Entries: 61                               │
│                                                                              │
│  ┌─ RECONCILIATION SUMMARY ──────────────────────────────────────────┐     │
│  │  Total Invoiced:       $4,270.00                                   │     │
│  │  Total Confirmed:      $4,200.00                                   │     │
│  │  Variance:             $70.00  ← 1 disputed entry                 │     │
│  │  Status: ⏳ Pending payment confirmation                           │     │
│  └────────────────────────────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Import:** Accountant uploads the Excel schedule received from SM Burhani. System auto-detects header row, maps columns, checks for duplicates by (PRN NUMBER + ENTRY REG).

**Add Station dialog:**
```
  Station Name:  [ Nakonde           ]
  Border Post:   [ Tanzania – Zambia ]
  Description:   [ _________________ ]
  [ Add Station ]
```

#### RahnTech

```
┌─ RECONCILIATION — RAHNTECH ────────────────────────────────────────────────┐
│                                                                              │
│  [⬆ Import RahnTech Report]  [🔍 Search...]  [Border ▾]    [⬇ Export]     │
│                                                                              │
│  S/N   SALES DATE          TRIP NUMBER       DEVICE NO    VEHICLE/CHASSIS  │
│  ─────  ──────────────────  ────────────────  ───────────  ───────────────  │
│  1      02 Dec 24 14:22     TZDL20240567864   5022610006   T151EFP/T611EGS │
│  2      02 Dec 24 14:19     TZDL20240567855   5022609662   T534EEQ/T734EEM │
│                                                                              │
│  ← cont →  DRIVER NAME      BORDER    GATE    DO NO                        │
│             HAMISI KAZEMBE   TUNDUMA   KICD    3056                          │
│                                                                              │
│  TOTAL TRIPS:  1,402   TOTAL CHARGES:  TBD (column not in current data)    │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

### 5.8 Management Panel

**Purpose:** Administrative control over the dynamic lists (categories, routes, stations) without needing admin role.

#### Category Manager

```
┌─ MANAGE — CATEGORIES ──────────────────────────────────────────────────────┐
│                                                                              │
│  [+ New Category]                                [🔍 Search categories...]  │
│                                                                              │
│  CASHIER-FED CATEGORIES (verified cashier data flows into these)            │
│                                                                              │
│  ●  Mileage              ▸ 3 routes    [Manage Routes]  [✏] [⏸ Archive]   │
│  ●  LATRA                             [✏] [⏸ Archive]                       │
│  ●  C28                               [✏] [⏸ Archive]                       │
│  ●  C40                               [✏] [⏸ Archive]                       │
│  ●  Carbon & Permit                   [✏] [⏸ Archive]                       │
│  ...                                                                         │
│                                                                              │
│  DIESEL STATIONS (accountant-entered, invoice-based)                        │
│  ●  Infinity              [✏] [⏸ Archive]                                   │
│  ●  Lake Zambia           [✏] [⏸ Archive]                                   │
│  ●  Lake Tunduma          [✏] [⏸ Archive]                                   │
│  ●  GBP Diesel            [✏] [⏸ Archive]                                   │
│  [+ Add Diesel Station]                                                      │
│                                                                              │
│  ○  ARCHIVED (3)  [Show ▾]                                                  │
└──────────────────────────────────────────────────────────────────────────────┘
```

**New Category dialog:**
```
  Category Name *    [ _____________________ ]
  Type *             ( ● Cashier-Fed   ○ Diesel Station   ○ Manual Entry )
  Color              [  #0077C5  🎨 ]
  Requires Receipt   [ ☐ ]
  Requires Truck     [ ☑ ]
  Sub-breakdown?     [ ☐ Enable sub-routes/sub-sections ]
  
  [ Create Category ]
```

#### Reconciliation Station Manager

```
┌─ MANAGE — RECONCILIATION STATIONS ─────────────────────────────────────────┐
│                                                                              │
│  SM BURHANI BONDS                           [+ Add Station]                 │
│  ──────────────────────────────────────────────────────                     │
│  ●  Nakonde       Tanzania–Zambia border    [✏] [⏸ Archive]                 │
│  ●  Kasumbalesa   Zambia–DRC border         [✏] [⏸ Archive]                 │
│  ●  Sakania       Zambia–DRC border         [✏] [⏸ Archive]                 │
│                                                                              │
│  [+ Add New Reconciliation Group]  ← e.g., add a second bonding company    │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 6. Data Flow & Verification Gate

```
                         CASHIER
                            │
                     submits entry
                            │
                            ▼
                  ┌─────────────────┐
                  │  transactions   │
                  │  verified=False │  ← MongoDB collection
                  └────────┬────────┘
                           │
                    accountant opens
                    Verify Inbox
                           │
                    ┌──────┴──────┐
                    │             │
                 APPROVE        REJECT
                    │             │
                    │        cashier sees
                    │        rejection note
                    ▼
         ┌──────────────────────┐
         │  transactions        │
         │  verified=True       │ ← same document, updated
         │  verified_by=acc._id │
         │  verified_at=now()   │
         └────────┬─────────────┘
                  │
         ┌────────┴─────────────────────────────┐
         │                                       │
         ▼                                       ▼
  Master Expenses View                Category Sub-Table View
  (all verified, all categories)      (filtered by category_name)

  Both read from the SAME transactions collection.
  No data duplication — views are queries, not copies.
```

---

## 7. MongoDB Collections (New / Extended)

### 7.1 Existing `transactions` — Fields to Ensure Exist

```python
# Already in model — confirm these are present:
verified: bool = False
verified_by: Optional[ObjectId] = None
verified_at: Optional[datetime] = None
rejection_reason: Optional[str] = None   # ADD THIS
month: Optional[str] = None              # ADD THIS  e.g. "Jan 25"
year: Optional[int] = None              # ADD THIS  e.g. 2025
```

### 7.2 New `separate_expenses` Collection

For Congo Expenses, Harrison Expenses, Ahmed Kimvi:

```python
{
  _id: ObjectId,
  expense_type: str,           # "congo_expenses" | "harrison" | "ahmed_kimvi"
  sheet_label: Optional[str],  # For Ahmed Kimvi: "02.06" (visit date label)
  s_no: Optional[int],
  date: datetime,
  truck_no: Optional[str],
  trailer_no: Optional[str],   # Harrison only
  description: str,            # "Particulars" for Ahmed Kimvi
  lpo_no: Optional[str],
  amount_usd: Optional[float],
  amount_kwacha: Optional[float],
  amount_tzs: Optional[float],
  approved_by: Optional[str],
  is_advance: bool = False,    # Ahmed Kimvi advance rows
  balance: Optional[float],    # Ahmed Kimvi running balance
  created_at: datetime,
  created_by: ObjectId         # accountant who entered
}
```

### 7.3 New `imported_feeds` Collection

For Toll Plaza, Parking Congo, Zambia Parking:

```python
{
  _id: ObjectId,
  feed_type: str,              # "toll_plaza" | "parking_congo" | "zambia_parking"
  import_date: datetime,
  imported_by: ObjectId,
  period_label: Optional[str], # e.g. "Week 10 2026"

  # Toll Plaza fields
  toll_date: Optional[datetime],
  toll_plaza: Optional[str],
  vehicle_reg: Optional[str],
  vehicle_class: Optional[str],
  tender_amount: Optional[float],
  receipt_no: Optional[str],   # unique key for dedup
  cashier_name: Optional[str],
  card_no: Optional[str],

  # Parking Congo fields
  transaction_date: Optional[str],
  transaction_type: Optional[str],
  serial: Optional[str],       # unique key for dedup
  vehicle_no: Optional[str],
  amount: Optional[float],
  balance: Optional[float],
  gate_in: Optional[str],
  gate_out: Optional[str],

  # Zambia Parking fields
  ticket_no: Optional[str],    # unique key for dedup
  plate_num: Optional[str],
  debit: Optional[float],
  credit: Optional[float],
  heading_to: Optional[str],
}
```

### 7.4 New `diesel_entries` Collection

```python
{
  _id: ObjectId,
  station: str,                # "infinity" | "lake_zambia" | "lake_tunduma" | "gbp"
  date: datetime,
  lpo_no: str,
  do_sdo_no: str,
  station_name: str,           # e.g. "LAKE KAPIRI", "GBP MOROGORO"
  destination: str,
  truck_no: str,
  litres: float,
  price_per_litre: float,
  total_amount: float,
  total_usd: Optional[float],  # for Lake Zambia
  remark: Optional[str],
  created_at: datetime,
  created_by: ObjectId
}
```

### 7.5 New `reconciliation_entries` Collection

```python
{
  _id: ObjectId,
  entity: str,                 # "sm_burhani" | "rahntech"
  station: str,                # "nakonde" | "kasumbalesa" | "sakania"
  schedule_period: str,        # "01.05.2026 - 15.05.2026"
  sr_no: int,
  sm_ref_no: str,
  prn_number: str,             # unique key for dedup
  entry_reg_no: str,
  t1_no: Optional[str],
  t1_date: Optional[datetime],
  importer: Optional[str],
  consignment: Optional[str],
  truck_and_trailer: str,
  charge: float,
  confirmed: bool = False,
  disputed: bool = False,
  dispute_note: Optional[str],
  import_date: datetime,
  imported_by: ObjectId
}
```

### 7.6 New `accountant_config` Collection

Flexible configuration for categories, routes, stations — all managed by the accountant:

```python
{
  _id: ObjectId,
  config_type: str,            # "category" | "mileage_route" | "council" | "diesel_station"
                               # "recon_station" | "separate_expense_type"
  name: str,                   # Display name
  parent: Optional[str],       # For sub-items: e.g. parent="mileage" for a route
  entity: Optional[str],       # For recon stations: "sm_burhani"
  color: Optional[str],
  active: bool = True,
  sort_order: int = 0,
  metadata: dict = {}          # Extra fields per type
}
```

---

## 8. Component Library

### 8.1 Shared UI Components

| Component | Description | Used In |
|-----------|-------------|---------|
| `SidebarWidget` | Collapsible nav with sections, badges, expand/collapse | All screens |
| `HeaderBar` | Top bar with logo, global search, notifications, user menu | All screens |
| `KPICard` | Stat card with icon, value, label, trend indicator | Overview, section headers |
| `DataTable` | Sortable, filterable, paginated table with frozen columns | All table views |
| `FilterBar` | Search + dropdown filters row | All table views |
| `ImportDialog` | Drag-drop file import with preview + duplicate handling | Toll Plaza, Parking Congo, SM Burhani, etc. |
| `EntryDialog` | Multi-field form dialog for manual entry | Diesel, Congo Expenses, etc. |
| `VerifyInboxRow` | Expandable transaction row with approve/reject actions | Verify Inbox |
| `ReceiptBadge` | Color-coded pill: Received/Pending/Missing/No Receipt | All tables |
| `MonthTabBar` | Jan–Dec filter tabs with per-month totals | Master Expenses, categories |
| `YearSelector` | Financial year dropdown (persists globally) | Header bar |
| `FooterTotals` | TZS + USD total bar pinned to bottom of tables | All table views |
| `ExpandableGroup` | Sidebar section with collapsible sub-items | Categories, Recon |
| `StatusBar` | DB connection, last sync, FY, version | Bottom of window |
| `ManageListPanel` | Add/edit/archive list items with drag reorder | Management panel |
| `ReconcileSummary` | Invoice vs. confirmed totals card with variance | SM Burhani views |

### 8.2 Table Behavior Standards

- **Row density toggle**: Compact (30px) · Default (36px) · Comfortable (44px) — persisted per table
- **Column visibility**: Right-click header to show/hide columns
- **Sort**: Single-click column header; shift-click for multi-column sort
- **Frozen columns**: S/NO, DATE, primary key column always visible
- **Pagination**: 25 / 50 / 100 / All rows selector bottom-left
- **Selection**: Checkbox column; Ctrl+A selects all on current page
- **Context menu**: View Detail · Copy Row · Export Selection
- **Empty state**: Centered illustration + "No records found" + action button
- **Loading state**: Skeleton rows (shimmer) while async fetch in progress

---

## 9. Implementation Phases

### Phase 1 — Core Infrastructure (Week 1)
- [ ] Create `AccountantDashboard` shell with sidebar + header + status bar
- [ ] Implement `SidebarWidget` with all sections, icons, collapse behavior
- [ ] Implement `HeaderBar` with global search stub and user menu
- [ ] Implement `StatusBar`
- [ ] Wire accountant role in `main_window.py` to launch new dashboard
- [ ] Create `accountant_service.py` (async service layer)

### Phase 2 — Verification Inbox (Week 1–2)
- [ ] Build `VerifyInboxWidget` with unverified transaction queue
- [ ] Implement single-row expand with full detail + category edit
- [ ] Implement approve action (sets verified=True, routes to master + category)
- [ ] Implement reject action with note back to cashier
- [ ] Implement bulk approve for auto-matched transactions
- [ ] Badge count on sidebar updates in real-time

### Phase 3 — Master Expenses Table (Week 2)
- [ ] Build `MasterExpensesWidget` with full column schema
- [ ] Year filter + month tab bar
- [ ] Sort, filter, paginate
- [ ] TZS/USD footer totals
- [ ] Export to Excel/CSV

### Phase 4 — Category Sub-Tables (Week 2–3)
- [ ] Standard category table (shared component, parameterized by category)
- [ ] Mileage special view with route cards + sub-filter
- [ ] C28/C40 unique column schema
- [ ] Diesel Cash table
- [ ] Council Fees tabbed view (Kapiri / Tunduma / Nakonde)

### Phase 5 — Diesel Stations (Week 3)
- [ ] `DieselStationWidget` (parameterized: Infinity, Lake Zambia, Lake Tunduma, GBP)
- [ ] Manual entry form (New Entry dialog)
- [ ] Excel import for existing data migration

### Phase 6 — Separate Expenses (Week 3–4)
- [ ] `TollPlazaWidget` with import from Dot Com Zambia Excel
- [ ] `ParkingCongoWidget` with import from Congo transporter ledger
- [ ] `CongoExpensesWidget` with manual entry form
- [ ] `AhmedKimviWidget` with visit-sheet pagination
- [ ] `ZambiaParkingWidget` with weekly statement import
- [ ] `HarrisonExpensesWidget` with USD + Kwacha columns

### Phase 7 — Reconciliation (Week 4)
- [ ] `SMBurhaniWidget` with station tabs (Nakonde, Kasumbalesa, Sakania)
- [ ] Schedule import dialog (SM Burhani Excel)
- [ ] Reconciliation summary card (invoiced vs. confirmed vs. variance)
- [ ] Dispute marking on individual rows
- [ ] `RahnTechWidget` with import

### Phase 8 — Management Panel (Week 4–5)
- [ ] `CategoryManagerWidget` (add/edit/archive categories + sub-routes)
- [ ] `ReconciliationStationManager` (add/edit/archive stations)
- [ ] `accountant_config` MongoDB CRUD via `accountant_config_service.py`
- [ ] Sidebar dynamically reflects accountant_config changes in real-time

### Phase 9 — Overview Dashboard (Week 5)
- [ ] KPI cards (pending, total entries, verified this month, TZS/USD totals)
- [ ] Monthly trend bar chart (use `pyqtgraph` or `matplotlib` embedded)
- [ ] Category pie chart
- [ ] Receipt status breakdown bar
- [ ] Recent activity feed
- [ ] Quick action buttons

### Phase 10 — Polish & QA (Week 5–6)
- [ ] Consistent styling pass (all colors, fonts, spacing match design system)
- [ ] Keyboard navigation on all tables
- [ ] Empty states and loading skeletons
- [ ] Error handling for failed DB calls (inline retry banners)
- [ ] Export to Excel for all tables (use `openpyxl`)
- [ ] Print view for Master Expenses (monthly summary)

---

## Design Reference Links

- [Intuit Design System — Color](https://design.intuit.com/quickbooks/brand/design-foundations/color/)
- [QuickBooks CRM UI Case Study](https://rondesignlab.com/cases/quickbooks-crm-management-branding-ux-ui-design)
- [Icons8 — Accounting Icon Set](https://icons8.com/icons/set/accounting)
- [IconScout — Accounting App Icons](https://iconscout.com/icons/accounting-app)
- [Material Design Icons (qtawesome `mdi.*`)](https://pictogrammers.com/library/mdi/)
- [PySide6 Tutorial 2026](https://www.pythonguis.com/pyside6-tutorial/)

---

*End of Plan — Version 1.0 · June 10, 2026*
