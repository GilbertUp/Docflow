# DocuFlow demo, about 5 minutes (plus extras)

Open http://127.0.0.1:5174. Organization `demo`. Passwords are in `.data/demo/credentials.txt`.
Dummy files are in `demo/documents/`.

**1. File an invoice (admin)**
Files, Finance, Supplier Invoices, Upload `invoice-ABC-1042.pdf`.
Say: "It is read as soon as it arrives. Nobody types anything."

**2. It fills itself in (admin)**
Row, Details. The type is already Supplier invoice. Invoice number INV-1042, due 2026-11-30 and total 1,042.50 are filled in.
Say: "Each value comes from the page, and a person can correct it."

**3. Look and find (admin)**
Row, View: the page. Then Search, type `consulting`: it finds the invoice and highlights the words.

**4. Ask a question (admin)**
Ask, type `What is the total of the ABC invoice?`
It returns the passage, labelled "Extracts from your documents. Nothing has been written", with the page and a link.
Say: "With no AI service set up it never pretends to write an answer. Turned on, answers are checked against their sources. It only ever uses documents you may read."

**5. Send it for approval (admin, then Felix)**
Row, Workflow, choose Invoice approval, Start.
Sign out. Sign in as Felix. The bell shows a task. Files, Finance, Supplier Invoices, Workflow, type "Matches the order", Approve.
Sign out. Sign in as admin. Workflow, Approve.
Say: "Two steps, in order, each with a deadline and a history."

**6. Sign and stamp (admin)**
Row, Sign, enter the password. Then View, pick the APPROVED stamp, click the page.
Say: "The signature is tied to this exact file. A new version would need a new one."

**7. Privacy punchline**
As admin, open HR, Employee Files, Upload `board-minutes-november.pdf`.
Sign out. Sign in as Felix. Search `budget`: nothing. Search `consulting`: the invoice. Ask `What is the annual budget?`: not enough evidence.
Say: "Search and answers only use what you may read."

## Extras, if there is time (admin)

- **Audit trail:** Audit trail. Filter "Action starts with" `document.`. Export CSV. "Check the trail is intact."
- **Compression:** Upload `scan-delivery-note.pdf`. Row, Compress, Make a smaller copy. About 80% smaller, every page checked, original untouched.
- **Retention and legal hold:** Row, Retention. Set class `Invoices 7 years`. Add to hold `Audit 2026`. Status shows "On legal hold".
- **Operations:** Operations. Health, usage, background jobs.
- **Forms:** Forms, Leave request. Try a bad date: every problem is shown at once.
- **Webhooks:** Webhooks. Try `http://localhost/x`: refused, internal addresses are not allowed.

**If it goes wrong**
- Nothing fills in: wait 5 seconds and reopen Details.
- Can't sign in: use the exact organization name `demo`.
- Compress shows nothing: wait about 5 seconds, the dialog refreshes itself.
