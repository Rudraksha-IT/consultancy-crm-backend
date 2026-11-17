---
status: accepted
date: 2026-11-17
---

# Documents live in private S3, are reached by short links, and go to a Bin before deletion

Recorded 2026-11-17 for decisions made in issues 16, 17, 78 and 79.

## Context

- Passports and bank statements are the most sensitive data in the product. Public or long-lived links would leak them.
- Checking the file's real content stops an HTML or script file being uploaded with a `.pdf` name.
- Staff delete the wrong file by mistake; an Admin check before anything is gone for good protects the office.

## Decision

- Document files are stored in a **private S3 bucket**, one prefix per Consultancy. Nothing is public.
- A file is reached only through a **signed link that works for 5 minutes**, issued after the permission check. The link also fixes the download's headers.
- Uploads are checked on the server by their **first bytes**, not their name: only PDF, JPEG and PNG, at most 10 MB per file, at most 10 files per upload, 20 files per Document and 200 Documents per Student. The stored name is cleaned and ends in the real type's extension.
- A Document holds **one or more files**, and Verification (Pending, Verified, Rejected with reason) covers the whole Document. A new file puts a Verified Document back to Pending.
- **The Bin:** when anyone but an Admin deletes a Document or file, they give a reason and it goes to the Bin instead of being removed. Nobody can open, download, check or use a binned item; only an Admin opens it from the Bin tab and restores it or deletes it for good (removing the stored files too). An Admin's own deletes skip the Bin. Nothing empties the Bin by itself.

## Consequences

- Locally, MinIO stands in for S3; in tests, storage is faked at its interface.
- The checklist counts a binned Document as missing.
