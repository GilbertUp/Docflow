# DocuFlow demo, about 5 minutes

Open http://127.0.0.1:5173. Organization `demo`. Passwords are in `.data/demo/credentials.txt`.
Dummy files are in `demo/documents/`.

**1. File an invoice (admin)**
Files, Finance, Supplier Invoices, Upload `invoice-ABC-1042.pdf`.
Say: "It is read as soon as it arrives. Nobody types anything."

**2. It fills itself in (admin)**
Row, Details. The type is already Supplier invoice. Invoice number INV-1042, due 2026-11-30 and total 1,042.50 are filled in.
Say: "Each value comes from the page, and a person can correct it."

**3. Look and find (admin)**
Row, View: the page. Then Search, type `consulting`: it finds the invoice and highlights the words.

**4. Send it for approval (admin, then Felix)**
Row, Workflow, choose Invoice approval, Start.
Sign out. Sign in as Felix. The bell shows a task. Files, Finance, Supplier Invoices, Workflow, type "Matches the order", Approve.
Sign out. Sign in as admin. Workflow, Approve.
Say: "Two steps, in order, each with a deadline and a history."

**5. Sign and stamp (admin)**
Row, Sign, enter the password. Then View, pick the APPROVED stamp, click the page.
Say: "The signature is tied to this exact file. A new version would need a new one."

**6. Privacy punchline**
As admin, open HR, Employee Files, Upload `board-minutes-november.pdf`.
Sign out. Sign in as Felix. Search `budget`: nothing. Search `consulting`: the invoice.
Say: "Search only returns what you may read."

**Spare:** Forms, Leave request. Notifications, Tasks.

**If it goes wrong**
- Nothing fills in: wait 5 seconds and reopen Details.
- Can't sign in: use the exact organization name `demo`.
