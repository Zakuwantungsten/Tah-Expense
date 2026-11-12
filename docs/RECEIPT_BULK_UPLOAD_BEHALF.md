# Receipts — bulk select, upload on behalf, mobile received

## Goals

1. Multi-select request rows → **Remind**, **Upload**, **Upload partial**, or **Link**.
2. One file (or many) → one or many selected request rows (desk on behalf of ownership).
3. When desk marks received, ownership person gets a **Receipt received** push; mobile shows received state.

## Phases

| Phase | Work |
| --- | --- |
| **1** | Requests: selection count + bulk Remind (existing) + Upload / Upload partial / Link selected |
| **2** | Upload pick → park → link to selected `transaction_id`s (full or partial) |
| **3** | Link tab: clearer N×M hints; jump from Requests with txs in mind |
| **4** | Server push on `mark_request_received`; mobile opens request detail |

## Link matrix (already supported by API)

| Files selected | Rows selected | Result |
| --- | --- | --- |
| 1 | N | One receipt covers N requests |
| N | 1 | N pages → one request |
| N | M | Full cross-link |
