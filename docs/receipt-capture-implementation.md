# Receipt capture: implementation brief

Prepared 2 November 2026 for further research. This is a product and engineering brief, not tax advice. The Tanzania and Kenya points below come from public statutes and a secondary note. Confirm them with the person who files the return before any rule is treated as final.

## 1. Decision

Keep each receipt file once, linked to the register rows it covers. When TRA or KRA asks for evidence, generate a folder pack by year, month, and item, plus a spreadsheet that maps every file to its rows.

The chase, the phone capture, and the accountant's "these receipts cover these rows" action are the right design. They match what Ramp, Brex, Concur, and Dext do. The folder tree is the handoff. It is not the live filing system.

Start with a phone page opened from a message to the ownership name. A native app store app comes after that page is actually being used. Matching and the follow-up list stay on the cashier and accountant desktop, which already exists.

## 2. The problem today

Receipts arrive in a WhatsApp group. Someone later scrolls the group, saves the pictures, sorts them, and tries to confirm two different things: whether the paper exists, and whether the money was collected or given out by the person named as ownership, or approved by someone else.

That follow-up slips. Files sit on phones. A chat photo is resized. There is no list of who still owes a paper, or why a paper will never come. A tax officer who asks for "all LATRA receipts for March" cannot be handed one folder that is known to be complete.

## 3. The idea, and what to keep from it

The working idea was:

1. A mobile app. The person named on the transaction (ownership) is told that a receipt is needed.
2. That person sends the paper in any format: photo, PDF, or other file.
3. On the cashier or accountant side, press and hold one or several receipts and say which transaction row, or which rows, they cover.
4. On the server, sort receipts by item (LATRA, inspection, and so on) into folders and month subfolders.
5. If TRA or KRA wants evidence, hand them that folder.
6. A place to track receipts, follow up, and record who did not send and why.
7. Flexibility: many files, many rows, several ways to send.

Keep 1 through 3, 6, and 7. Change 4 and 5 so the folders are built on demand from the links, not by moving the live file into a folder. Change the first build of 1 so the phone page does not require an app-store install. The same page can become an app later.

Why the folder is not the record:

- One LATRA receipt often covers several rows. A folder copy per row creates several originals.
- Several photos can belong to one row. A single folder slot hides that.
- If someone corrects the item from LATRA to inspection, a live folder has to be moved or the path is wrong. The item and the month are already on the row, so the pack can be rebuilt.
- A folder does not remember who was asked, when they were reminded, or why a receipt was waived.

## 4. What this app already has

Tahmeed is a desktop register (cashier and accountant) with a FastAPI backend and MongoDB. The receipt process should extend these pieces. It should not add a second ledger.

| Already there | Where | What it does today | What is missing |
| --- | --- | --- | --- |
| Receipt status | `receipt_status` on the transaction. Normalized in `backend/app/import_ops.py` (`normalize_receipt`) | `pending` (default), `received`, `missing` (shown as No Receipt) | Typed by hand. Adding a file does not set it to received |
| Ownership | `ownership` on the row | Free text. This is the person who collected or paid out | Not a person record, so there is no phone number to notify |
| Approved by | `approver` on the row | Free text | Separate from ownership, which is correct. It is not the receipt |
| Item requires a receipt | `requires_receipt` on the category / item | A checkbox in Manage Items | Verify does not refuse a pending receipt |
| Attachments | `backend/app/attachments_api.py`, `backend/app/attachment_ops.py`, `tahmeed/services/attachment_service.py` | File is stored on the API machine at `var/attachments/<transaction id>/`. Desktop caches a copy under LocalAppData. Two-step save: stage, write Mongo, then rename into place | Only after someone already knows the row. No inbox of unmatched files |
| Allowed types and size | `attachment_ops.py` | PDF, png, jpg, jpeg, gif, webp, bmp, tif, tiff, doc, docx, xls, xlsx, csv, txt. Maximum 25 MB | No HEIC, which is what many iPhones produce |
| Receipt counts | `backend/app/transactions_api.py` | Counts of received, pending, and missing | No chase list by person or age |
| Master Expenses filter | `tahmeed/ui/accountant/master_expenses.py` | Filter Received, Pending, No Receipt | The follow-up still starts from a person scrolling |
| Cloud backup | `backend/app/cli/backup.py` | `mongodump` archive, optional S3 when `BACKUP_S3_BUCKET` is set. Client is boto3 | The archive contains attachment metadata. The image bytes stay on the API disk. A database restore does not bring the photos back |
| Audit of attachment add/remove | `attachments_api.py` writes `operation_events` | Who uploaded or deleted a file | No request, no reminder, no waiver reason |

Attachment metadata today is: `id`, `filename`, `stored_name`, `size`, `uploaded_at`, `uploaded_by`. There is no checksum, no cloud key, and no link that lets one file cover many rows. The array lives on the transaction, so one file on many rows would be stored many times.

## 5. How the large products do this

They all start from a transaction that already exists. The receipt is evidence for that transaction. The person responsible is asked directly. Unmatched files wait in an inbox. A human can override the match. A missing paper is a recorded decision, not an empty slot.

| Step | Ramp | Brex | Dext | Concur | What Tahmeed should copy |
| --- | --- | --- | --- | --- | --- |
| The spend exists first | Card transaction. Policy says if a receipt is required | Same | A document can also create the bill, or attach to one that exists | Expense line, then the receipt store | The cashier save is that moment |
| Ask one person | SMS for an in-person spend. Email for an online one. Push if the app is on. A WhatsApp link opens a mobile submission page | Push or SMS about 2–7 days later if still missing. Email every Monday until it is done | The capture channel is the inbox (app, email, upload) | The employee owns the expense report | Message the ownership name. Repeat on a visible schedule. Do not ask the whole group |
| Send the file | Photo reply, app camera, email to receipts@ramp.com, or the WhatsApp link. PDF, PNG, JPG, JPEG, HEIC, WebP. Web upload cap 20 MB. PDF cap 50 pages | Camera, document scanner, photo library, or files. Up to 10 at once in the app, up to 500 in the dashboard | App photo, forwarded email, or a dropped folder | Many images into the receipt store, then attach | Phone page: camera or file. Any type this server already allows, plus HEIC |
| Match | Automatic from the receipt contents. One receipt may be attached to several transactions (two Amazon charges, one receipt). If the guess fails, the person uploads onto the transaction | Matches when two of date, amount, and merchant agree. Unmatched files stay for a person to review | Paperwork Match suggests a transaction from supplier, total, and date (date may be up to 10 days later, and only the last 6 months). A person confirms. Only the image is sent | Each stored image is attached to one expense. The user picks it | Suggest from amount, date, and ownership. The accountant selects files and rows and confirms. One file may cover many rows. Many files may cover one row |
| No paper | "I don't have a receipt" on the transaction | Lost-receipt affidavit. Some categories (airlines, hotels, car rentals in their US policy) cannot use it | The item stays unmatched or is published without an image only if the firm allows it | Missing receipt declaration | Status Waived, with a reason and who accepted it. An item can be marked so it cannot be waived |
| Follow-up | Finance sees outstanding receipts before month-end and can chase inside the product | The Monday email continues until action | Missing-paperwork reports show what is still out | The report cannot close cleanly while a required receipt is absent | Open requests grouped by ownership name, with age |
| Storage and audit handoff | The transaction holds the file | Same | The image stays in Dext and is attached to the accounting transaction | Receipt image on the expense | File stored once by id. Export builds the folders and the index |

Sources for this table are listed in section 12. Product behaviour changes. Re-read the help pages before copying a limit (file size, reminder timing, which categories can be waived).

What not to copy from them:

- A full expense app that retypes the Tahmeed ledger. The register is already the book.
- Automatic match as the only path. Thermal receipts, handwritten notes, and shared receipts will miss. The suggestion is a help. The link is a human confirm.
- Their card feed. Tahmeed does not have one. The saved row is the equivalent event.

## 6. Tax evidence, for the research

This section is so the pack is shaped like something an officer can use. It is not a ruling.

**Tanzania.** The Tax Administration Act, section 43, requires documents, on paper or electronic, that contain what must be filed, and that let tax be determined accurately. They are kept for five years from the relevant date. For income tax that date is the end of the year of income. If there is an objection, an appeal, a refund application, or an audit, the documents stay until that matter is finished. A person who keeps documents electronically must keep a primary data server for those documents inside the United Republic.

**Tanzania, deductions.** A PKF note of 15 July 2024 on the Finance Act 2024 describes an amendment to section 11 of the Income Tax Act: expenditure claimed as a deduction is expected to be supported by an EFD receipt or a fiscal tax invoice. The note names two exceptions: goods or services from a non-resident with no permanent establishment in Tanzania, and a person who is not VAT-registered and whose turnover is under TSh 11 million, where the Commissioner has allowed a lighter rule. Read the Act and the current practice notice before encoding "cannot waive" on an item. The product should allow an item to require a fiscal receipt and to refuse a waiver. Which items get that flag is an accounting decision.

**Kenya.** The Tax Procedures Act, section 23, requires documents that let the tax liability be readily ascertained, in English or Kiswahili, kept for five years from the end of the reporting period, longer if an amended assessment or a proceeding is still open. If a document is in another language, the Commissioner can require a translation. KRA case material on section 59 and section 23 puts the burden on the taxpayer to produce the records. An explanation for a missing record only helps if the Commissioner accepted it inside the five years.

**What that means for the build**

- Keep originals for at least five years, and do not let the backup rotation delete them while an audit flag is set.
- The primary electronic copy sits on a server in Tanzania. S3, if the bucket is outside the country, is the second copy.
- The file handed over should be the camera or scanner file. A picture that has already passed through WhatsApp is compressed. That is a weak copy of an EFD.
- The pack includes rows that have no file, with the waiver reason. An empty folder looks like a missing expense. A row in the index that says "waived, no EFD, accepted by …" is an explanation.
- The index is in a language the officer can read. English is the safe default for a KRA request. Swahili labels can be added. Do not rely on a folder of images with phone names (`IMG_2044.jpg`).

## 7. Target workflow

1. The cashier saves the row with date, amount, payee, item, ownership, and approver. If the item requires a receipt and nothing is linked, the server opens a receipt request in status Needed.
2. The ownership person receives one message for that request. The message shows the date, the amount, and the item, and contains a link.
3. The link opens a phone page. The person takes a photo or chooses a file. They can send several files for the same request. They can also say they have no receipt, and must type a reason. Sending no reason does not waive the row.
4. Files land in the receipt inbox, tied to that request when they came from the link. A file dropped on the desktop, with no request, lands in the same inbox as unmatched.
5. The accountant (or the cashier) selects one or more inbox files and one or more rows, then links them. The screen offers a suggestion when the amount, the date, and the ownership are close. The person still confirms.
6. Linking the last required file moves the request to Received and sets the row's receipt status to received. A waiver moves it to Waived and sets the row to missing, and stores the reason.
7. Reminders go to the ownership person while the request stays Needed. The accountant sees the open list without opening WhatsApp.
8. Verify refuses a submitted row whose item requires a receipt while the request is still Needed.
9. When an officer asks, the accountant exports a pack for a year, a month, an item, or a mix. The pack is folders plus `index.csv`. Generating it does not move the live files.

Same-day rule, which can start before the software exists: the row is saved when the cash moves, the person named in ownership sends the paper that day, and the end of the day is the Pending list chased by name. A useful caption until the link exists is `02-11 | 45000 | fuel | ALI | JUMA` (date, amount, item, ownership, approver).

## 8. Records to add

These are proposed. They are not in the database yet.

### 8.1 Receipt file

One document per uploaded file, not nested only inside one transaction.

| Field | Purpose |
| --- | --- |
| `_id` | File id. Also the stored object name |
| `original_filename` | What the phone called it |
| `content_type` | Detected type, not only the extension |
| `size` | Bytes |
| `sha256` | Checksum. The export index prints it. The S3 upload stores it as metadata, the same way the Mongo backup already checks its archive |
| `stored_path` | Local path under the attachments directory |
| `s3_key` | Set after the second copy succeeds. Empty means the cloud copy is still due |
| `uploaded_at`, `uploaded_by` | Who sent it, if they were logged in. A phone-link upload stores the request id instead of a user id |
| `source` | `phone_link`, `desktop`, `inbox_drop`. Later: `whatsapp` |
| `inbox_status` | `unmatched` or `matched` |
| `request_id` | Set when the upload came from a request link. Still unmatched until a person confirms the rows |

The current `attachments` array on the transaction becomes a list of links (`file_id`, `linked_at`, `linked_by`), not a private copy of the bytes. Existing single-row attachments can stay readable. New writes go through the file record. A later migration can copy old files into this shape. Do not block the first release on rewriting old files.

### 8.2 Receipt request

One request per row that needs a receipt. A row that does not require a receipt has no request.

| Field | Purpose |
| --- | --- |
| `transaction_id` | The register row |
| `asked_of_name` | The ownership text at the time of the ask |
| `asked_of_phone` | From the person directory, once it exists. Empty until then |
| `status` | `needed`, `received`, `waived` |
| `opened_at` | When the row was saved |
| `last_reminded_at` | Last chase |
| `reminder_count` | How many chases |
| `waiver_reason` | Required when status is waived |
| `waived_by` | User id of the accountant or cashier who accepted the waiver |
| `waived_at` | When |
| `file_ids` | Files linked to this row. May be several |

Status rules:

- `needed` until at least one file is linked, or a waiver is accepted.
- Linking a file sets `received` and sets the row `receipt_status` to `received`. Removing the last file returns the request to `needed` and the row to `pending`, unless a waiver is in force.
- An explicit No Receipt on the row, with a reason, is a waiver. The old value `missing` remains the row's receipt status so current filters keep working.
- A second identical save of the same row must not open a second request.

### 8.3 Person to notify

Ownership is free text today. A notification needs a phone. Add a small people list: display name, phone in international form, and whether they receive receipt requests. The register keeps storing the ownership name it stores now. The request looks up the phone by that name.

Until the list exists, the accountant copies the link and sends it. The request and the inbox still work. Do not block filing on the directory.

### 8.4 Item policy

Use the existing `requires_receipt` flag. Add one more flag when the research on EFD is done: `waiver_allowed` (default true). Fiscal items can set it false. Verify and the phone page then refuse "I have no receipt" for that item.

## 9. Storage

| Copy | Place | Rule |
| --- | --- | --- |
| Primary | API disk, `var/attachments/files/<file id>` | Written first, with the same stage-then-rename the attachment code uses now. This is the copy that must sit in Tanzania if the documents are electronic |
| Open cache | Desktop, LocalAppData, as today | So Open does not download again |
| Second | S3, key `receipts/<file id>` | Upload after the local rename succeeds. Reuse the boto3 client in `backend/app/cli/backup.py`. Store sha256 in object metadata. A failed upload leaves `s3_key` empty and a retry job picks it up. Do not delete the local file because the upload failed |
| Database archive | Existing mongodump | Still the ledger backup. It is not the photo store |
| Evidence pack | A zip generated on request, not a live directory | Safe to delete after it is downloaded. Regenerating it from the links produces the same index |

Do not store the receipt only in WhatsApp. Meta's media URL expires in about 5 minutes. A media id delivered by webhook lasts about 7 days (Meta media documentation, updated 16 June 2026). After that, Meta does not have the file.

Object key is the file id, not `year/month/item/...`. The year, month, and item live on the transaction and can change. The export path is computed at pack time:

```text
2026/03/LATRA/2026-03-02_45000_ALI_<file id>.jpg
```

The name is for a person browsing the zip. The id at the end keeps two files on the same day from colliding. The index is the authority.

`index.csv` columns:

- pack path
- original filename
- sha256
- size
- transaction id
- date
- amount
- currency
- item
- payee
- ownership
- approver
- receipt status
- request status
- waiver reason
- waived by
- row has no file (yes/no)

A file that covers three rows appears once in the zip and three times in the index, or once in the index with the three transaction ids listed. Prefer one index line per row, including rows with no file, so the officer can see the gap. Prefer one physical file in the zip.

## 10. Phone page

One page, no login, opened by a link that contains an unguessable token for that request. The token expires or can be rotated. The page shows only that request: date, amount, item, payee. It does not show the rest of the ledger.

Actions:

- Take a photo (camera input).
- Choose a file. Accept the extensions in section 4, plus `.heic`.
- Send several files before finishing.
- "I do not have the receipt", with a reason box. Hidden when `waiver_allowed` is false.

The page posts to the existing API. Bytes are stored with the same size cap and the same two-step write. The request stays Needed until a person on the desktop confirms the link, except when the upload was made from this request's own link and the request is for exactly one row: that case may auto-link, because the person was asked about that row. Files dropped with no request never auto-link.

A native app, later, is this page plus a push notification and the camera. Build it when the link is in daily use and the missing piece is "they do not see the message". Do not start the project in an app store.

WhatsApp remains the transport for the link, because that is where these people answer. The group can stay for ordinary talk. The file should be uploaded on the page so the bytes are the camera file. Forwarding a chat photo into the group, and then saving it from the group, is the path that compresses an EFD.

A WhatsApp Business webhook that downloads media into the same inbox is a later channel (`source = whatsapp`). It uses the same file record. It is not a different archive. Do not scrape WhatsApp Web.

## 11. Desktop: inbox, link, and chase

On the cashier and accountant desktop:

**Inbox.** Unmatched files. Thumbnail or filename, time received, source, and the request they came from if any. Multi-select.

**Link.** Select files, select rows (current day or a search by date, amount, ownership, item), confirm. One action writes the links. The row's receipt status updates from the links, as in section 8.2.

**Suggestion.** When a file's request has an amount and a date, offer that row at the top. When there is no request, offer rows with the same amount and a date within a few days. Show them as suggestions. Do not link them in the background.

**Chase.** Requests in `needed`, grouped by ownership name. Columns: name, open count, oldest age, last reminder, amount still open. From a row, copy the link or mark reminded. Waived requests are a second list: reason, who accepted, item. Verify reads the request. A `needed` request on an item with `requires_receipt` blocks verify. A waived request does not, unless `waiver_allowed` is false.

**Press and hold.** That gesture is the phone version of multi-select. On the desktop, multi-select is the same action. Both write the same link.

The accountant does not file by dragging images into LATRA and inspection folders. They link. The export builds the folders.

## 12. Evidence pack

An accountant action: choose a date range, and optionally an item, a payee, or an ownership name. The server builds a zip.

Contents:

- `index.csv` as in section 9, one line per row in the range, file or no file.
- The folder tree `year/month/item/filename`.
- A short `README.txt` with the range, the time the pack was built, and the number of rows with a file, without a file, and waived.

The zip is a download. It is not kept as the master copy. If a link changes after the export, the next export shows the change. The previous zip, if the officer already has it, is a snapshot. Store the pack's sha256 and the time it was built on an export record if a later question is "what did we give them".

Retention: do not apply the Mongo backup's "keep the newest N archives" rule to receipt files. Those files follow the five-year rule, extended while a request or a year is marked under audit.

## 13. Build order

Each step is usable on its own. Later steps assume the earlier ones.

### Phase 1. The row tells the truth

- On upload of a file to a row that is `pending`, set `receipt_status` to `received`.
- If the last file is removed, set it back to `pending`.
- A status that was explicitly `missing` stays `missing` until someone clears it.
- Opening a request in `needed` when the item `requires_receipt` and the row has no file.
- Chase list: those requests, grouped by ownership.
- Verify refuses `needed`.
- After the local file is in place, upload the bytes with the existing S3 client. Record `s3_key` and `sha256`.
- Add HEIC to the allowed types, stored as HEIC or converted to JPEG on the server. Converting is easier for an officer who does not have a tool that opens HEIC. Keep the checksum of the original and of the converted file if both are kept.

No phone page in this phase. People can still attach from the desktop, and the Pending list is finally a chase list.

### Phase 2. The phone page and the inbox

- Token link per request.
- Phone page: camera, file, several files, waiver with reason.
- Inbox of unmatched files.
- Multi-select link of files to rows, including one file to many rows.
- Auto-link only for a file that arrived on that row's own link.
- Person list with phone numbers, optional. Without it, the accountant sends the link.

### Phase 3. The pack

- Export zip: folders by year, month, and item, plus `index.csv` and the readme.
- Export record: who built it, the range, the checksum.
- Audit hold that keeps files past the normal five years.

### Phase 4. Only if phase 2 is in daily use

- WhatsApp Business number whose webhook saves media into the same inbox, downloaded immediately, not by storing Meta's URL.
- Or a thin native app that opens the same page and sends a push.
- OCR that suggests amount and date. Useful after the inbox volume makes matching slow. Handwritten and thermal receipts will still need a person. Do not wait for OCR before phase 1.

## 14. What this will not do

- A second bookkeeping product that the rows are retyped into.
- A live directory of folders as the system of record.
- An unofficial WhatsApp Web client that reads the group.
- Scraping or storing the group chat as the archive.
- Automatic match with no confirm, except the single case in section 10 (the file arrived on that row's link).
- Deleting the local file because the cloud copy succeeded, during the five-year period. The local disk is the primary store.
- Blocking the register save because the receipt has not arrived. The row is the event. The request is the follow-up. Cash should still be recorded the same day.

## 15. Questions still worth researching

1. Which items must carry an EFD, and which may be waived, under the current Income Tax Act practice for this company. That decides the `waiver_allowed` flag. The PKF note is a start, not the flag list.
2. Whether the primary server is physically in Tanzania, or only the laptops are. Section 43(7) cares about the electronic primary store.
3. The real list of ownership names and the phone each one answers. Free text will not match a phone until those names are stable.
4. Whether one receipt commonly covers many rows (a fuel issue, a LATRA payment split across trucks). The data model allows it. The phone page should say so if it is the common case.
5. WhatsApp Business Platform cost, a dedicated number, and whether message templates are required for the first outbound "receipt needed" message. The phone-page link can be pasted by the accountant until that is known. Pricing and template rules change. Read Meta's current WhatsApp pricing page before committing.
6. HEIC. Confirm how many senders use iPhones. If almost all senders are Android, JPEG from the camera is enough and HEIC support can wait.
7. The officer's actual request format the last time TRA or KRA asked: a sample of transactions, a month, or a category. Build the export filters to match that request, not every filter that might exist.
8. Five-year disk size. 25 MB is the cap per file. Estimate from a real month of receipts before buying storage. The Mongo backup retention must not be reused as the file retention.

## 16. Sources

Code in this repository:

- `backend/app/attachment_ops.py`, `backend/app/attachments_api.py` — file types, 25 MB cap, disk layout, two-step save.
- `tahmeed/services/attachment_service.py` — desktop upload and local cache.
- `tahmeed/ui/dialogs/attachment_dialog.py` — add, open, remove on one saved row.
- `backend/app/import_ops.py` — `normalize_receipt` (`pending`, `received`, `missing`).
- `backend/app/schemas.py` — `receipt_status`, `ownership`, `approver`, `requires_receipt`.
- `backend/app/transactions_api.py` — counts of received, pending, and missing.
- `tahmeed/ui/accountant/master_expenses.py` — receipt filter on the ledger.
- `backend/app/cli/backup.py` — mongodump and optional S3. Photos are not in the archive.
- `backend/app/config.py` — `attachments_directory`, `backup_s3_bucket`, `backup_s3_prefix`.

Outside:

- Ramp, submitting receipts: https://support.ramp.com/submitting-receipts-memos-and-accounting-for-your-ramp-transactions — channels (app, SMS, email, WhatsApp link), file types, one receipt on several transactions, "I don't have a receipt".
- Ramp, overview of missing-receipt follow-up: https://ramp.com/receipt-automation
- Brex, receipts: https://www.brex.com/support/receipts-for-expenses — camera and files, bulk upload, match on two of date, amount, and merchant, lost-receipt affidavit.
- Brex, reminders: https://www.brex.com/support/brex-notifications — push or SMS, and a Monday email until the receipt or memo is in.
- Dext Paperwork Match: https://help.dext.com/en/articles/377069-how-to-attach-a-document-to-an-existing-transaction-with-paperwork-match — attach an inbox image to an existing transaction. Suggestion uses supplier, amount, and date.
- Concur receipt store: https://community.concur.com/t5/Support-and-FAQs/Can-I-Upload-Multiple-Receipts-to-My-Receipt-Store-at-One-Time/ba-p/70220 — many images uploaded, then attached to expenses.
- Audit export shape (Xero users, the same problem): https://unapprove.atesensoftware.com/guides/export-receipts-for-audit-xero — folders by type, year, and month, plus a manifest that maps each file to its transaction. A file named by the scanner is not evidence on its own.
- Meta, WhatsApp media: https://developers.facebook.com/documentation/business-messaging/whatsapp/business-phone-numbers/media — download the file yourself. The URL expires in 5 minutes. A media id from a webhook lasts 7 days. Page updated 16 June 2026.
- Tanzania, Tax Administration Act (revised), section 43: https://oagmis.oag.go.tz/portal/acts/revised/538/download — five years, longer during a dispute or audit, primary electronic server in the country.
- PKF, 15 July 2024, fiscal receipts and the Finance Act 2024: https://www.pkfea.com/media/vd2kcv24/tz-article-15-july-2024-1.pdf — EFD or fiscal invoice for deductible expenditure, and the two exceptions the note describes.
- Kenya, Tax Procedures Act, section 23: https://www.kra.go.ke/images/publications/Tax-Procedures-Act--2026.pdf — five years from the end of the reporting period, English or Kiswahili, records sufficient to ascertain the tax.
- KRA case digest on the duty to produce records: https://www.kra.go.ke/images/publications/KRA-Cases-Digest-Issue-5.pdf

Re-check the statute PDFs and the product help pages when this brief is turned into a build. Limits and tax wording move.
