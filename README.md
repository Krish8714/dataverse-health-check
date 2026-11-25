# Dataverse Health Check

A small, **read-only** tool that scans a Dataverse / Dynamics 365 environment for common configuration mistakes and gives you a score out of 100 with a simple report.

It only sends GET requests. It never creates, changes or deletes anything.

> **Status: preview (v0.1).** Tested on a real Dataverse lab environment. Feedback and issues welcome.

See a [sample report](examples/sample-report.md).

## What it checks (v1)

| ID | Severity | What it looks for |
| --- | --- | --- |
| PLUGIN-01 | High | Synchronous Update plug-in steps with no filtering attributes (they run on every save) |
| PLUGIN-02 | Low | Custom plug-in steps left disabled |
| PLUGIN-03 | High | Plug-in assemblies not registered in the sandbox |
| PLUGIN-04 | Medium | Plug-in trace logs that contain exceptions |
| TRACE-01 | Medium | Plug-in trace log set to "All" |
| AUDIT-01 | High | Auditing turned off for the environment |
| AUDIT-02 | Low | Custom tables with auditing turned off |
| JOBS-01 | Medium / High | System jobs that failed in the last 7 days |
| LOGIC-01 | Medium | Active real-time (classic) workflows |
| SEC-01 | Medium | More than 5 active users with System Administrator |
| ALM-01 | Info | Unmanaged solutions (fine in dev, a warning sign in test or production) |

## Two ways to run it

### A. Inside Dynamics (no install)

1. Upload `web/dvhealth.html` as an HTML web resource (for example `new_/tools/dvhealth.html`) and publish it.
2. Open `https://yourorg.crm.dynamics.com/WebResources/new_/tools/dvhealth.html`.
3. Press **Run scan**. It uses your current sign-in, so you see only what your security role allows.

### B. From your computer (Python)

```bash
pip install msal requests
python dvhealth.py https://yourorg.crm.dynamics.com
```

It only looks at **your** components: tables with your publisher prefixes, and plug-in assemblies not signed by Microsoft. Use `--prefix lh` (repeatable) to set the prefixes yourself.

Sign-in uses the first option that works:

1. A token in the `DV_TOKEN` environment variable
2. The Azure CLI (`az login` first)
3. A device-code sign-in in your browser

The report is written to `dataverse-health-report.md`.

## Scoring

The score starts at 100. Each High finding removes 15 points, each Medium 7, each Low 2. Info findings don't change the score.

## Run it with a user who can read the environment's settings

System Customizer or System Administrator works best. With fewer privileges some checks can't run; they are listed at the end of the report instead of stopping the scan.

## Ideas for the next version

- Tables close to the limit for columns or relationships
- Security roles with Organization-level Delete on many tables
- Flows owned by users who have left
- Web resources not used on any form

## Author

Karthik Ithakota, Dynamics 365 & Power Platform Technical Lead (PL-600). Blog: https://krish8714.github.io
