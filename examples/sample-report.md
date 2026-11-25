# Dataverse Health Check

**Environment:** https://loanhub-lab.crm8.dynamics.com  
**Scanned:** 2026-11-25 17:17 UTC  
**Score:** 83 / 100  
**Your publisher prefixes:** cr09d, lh, new  
**Your plug-in assemblies:** 0

| High | Medium | Low | Info |
| --- | --- | --- | --- |
| 1 | 0 | 1 | 1 |

## [High] AUDIT-01: Auditing is turned off for the whole environment

No changes are being recorded, so nobody can see who changed what.

**How to fix:** Turn on auditing in the Power Platform admin center, then enable it on the tables that matter.

## [Low] JOBS-01: 6 system job(s) failed in the last 7 days

Examples:
- Post To Yammer job
- Processing modern flow following import
- Reprovisioning Job For 1033

**How to fix:** Open Settings > System Jobs, filter on Failed, and fix the most common error first. Some failures are platform jobs (for example after an import); check before acting.

## [Info] ALM-01: 2 unmanaged solution(s) in this environment

- Cr12a96
- LoanHub
This is normal in a development environment, but a warning sign in test or production.

**How to fix:** In test and production, import managed solutions built by a pipeline.

---
Read-only scan: this tool only runs GET requests and never changes your environment.