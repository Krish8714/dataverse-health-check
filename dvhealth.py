#!/usr/bin/env python3
"""
Dataverse Health Check
A read-only scan of a Dataverse / Dynamics 365 environment for common
configuration mistakes. It only runs GET requests: it never changes anything.

Usage:
    python dvhealth.py https://yourorg.crm.dynamics.com
    python dvhealth.py https://yourorg.crm.dynamics.com --out report.md

Sign-in (first one that works):
    1. DV_TOKEN environment variable (a bearer token you already have)
    2. Azure CLI:  az login   (the tool calls az account get-access-token)
    3. Device code sign-in in the browser (MSAL)
"""

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys

import requests

API = "/api/data/v9.2/"
# Microsoft's public sample client id for Dataverse (used in Microsoft Learn samples)
SAMPLE_CLIENT_ID = "51f81489-12ee-4a9e-aaae-a2591f45987d"

SEVERITY_POINTS = {"High": 15, "Medium": 7, "Low": 2, "Info": 0}

# Microsoft signs its own plug-in assemblies with this public key token.
MICROSOFT_KEY_TOKEN = "31bf3856ad364e35"
MICROSOFT_PUBLISHER = ("microsoft", "dynamics 365", "dynamics365", "adxstudio", "firstparty")


class Scope:
    """Works out which components are yours, so Microsoft's own built-in
    components (thousands of them) don't flood the report."""

    def __init__(self, dv, prefixes=None):
        if prefixes:
            self.prefixes = [p.strip().lower() for p in prefixes]
        else:
            pubs = dv.all("publishers?$select=uniquename,friendlyname,customizationprefix")
            self.prefixes = sorted({
                p["customizationprefix"].lower() for p in pubs
                if p.get("customizationprefix")
                and not any(m in (p["uniquename"] + " " + p["friendlyname"]).lower() for m in MICROSOFT_PUBLISHER)
            })
        asm = dv.all("pluginassemblies?$select=pluginassemblyid,name,isolationmode,publickeytoken"
                     "&$filter=customizationlevel eq 1")
        self.assemblies = [a for a in asm if (a.get("publickeytoken") or "").lower() != MICROSOFT_KEY_TOKEN]

    def is_mine(self, logical_name):
        return any((logical_name or "").lower().startswith(p + "_") for p in self.prefixes)


# ---------------------------------------------------------------- sign-in
def get_token(org_url: str) -> str:
    if os.environ.get("DV_TOKEN"):
        return os.environ["DV_TOKEN"]

    if shutil.which("az"):
        try:
            out = subprocess.run(
                ["az", "account", "get-access-token", "--resource", org_url, "-o", "json"],
                capture_output=True, text=True, timeout=60, check=True,
            )
            return json.loads(out.stdout)["accessToken"]
        except Exception:
            pass  # fall through to device code

    import msal
    app = msal.PublicClientApplication(SAMPLE_CLIENT_ID, authority="https://login.microsoftonline.com/organizations")
    flow = app.initiate_device_flow(scopes=[org_url.rstrip("/") + "/.default"])
    if "user_code" not in flow:
        sys.exit("Could not start device code sign-in: " + json.dumps(flow))
    print(flow["message"], flush=True)
    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        sys.exit("Sign-in failed: " + result.get("error_description", "unknown error"))
    return result["access_token"]


# ---------------------------------------------------------------- client
class Dataverse:
    def __init__(self, org_url: str, token: str):
        self.base = org_url.rstrip("/") + API
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": "Bearer " + token,
            "Accept": "application/json",
            "OData-MaxVersion": "4.0",
            "OData-Version": "4.0",
            "Prefer": 'odata.include-annotations="*"',
        })

    def get(self, query: str) -> dict:
        r = self.s.get(self.base + query, timeout=120)
        if r.status_code != 200:
            raise RuntimeError(f"{r.status_code} on {query.split('?')[0]}: {r.text[:300]}")
        return r.json()

    def all(self, query: str) -> list:
        data = self.get(query)
        rows = data.get("value", [])
        while data.get("@odata.nextLink"):
            r = self.s.get(data["@odata.nextLink"], timeout=120)
            r.raise_for_status()
            data = r.json()
            rows += data.get("value", [])
        return rows


# ---------------------------------------------------------------- checks
# Each check returns a list of findings: dict(id, severity, title, detail, fix)

def your_steps(dv, scope, extra_filter):
    """Plug-in steps whose assembly is yours (not signed by Microsoft)."""
    steps = []
    for a in scope.assemblies:
        types = dv.all(f"plugintypes?$select=plugintypeid&$filter=_pluginassemblyid_value eq {a['pluginassemblyid']}")
        for t in types:
            steps += dv.all(
                "sdkmessageprocessingsteps?$select=name,mode,stage,filteringattributes,statecode"
                f"&$filter=_plugintypeid_value eq {t['plugintypeid']} and {extra_filter}"
                "&$expand=sdkmessageid($select=name),sdkmessagefilterid($select=primaryobjecttypecode)"
            )
    return steps


def check_sync_update_without_filter(dv, scope):
    steps = your_steps(dv, scope, "mode eq 0 and statecode eq 0")
    bad = [s for s in steps
           if (s.get("sdkmessageid") or {}).get("name") == "Update" and not s.get("filteringattributes")]
    if not bad:
        return []
    lines = [f"- {s['name']} (table: {(s.get('sdkmessagefilterid') or {}).get('primaryobjecttypecode', '?')})" for s in bad]
    return [{
        "id": "PLUGIN-01", "severity": "High",
        "title": f"{len(bad)} synchronous Update plug-in step(s) have no filtering attributes",
        "detail": "These steps run on every save of the record, even when an unrelated column changes.\n" + "\n".join(lines),
        "fix": "Set filtering attributes on each step to only the columns the plug-in actually needs.",
    }]


def check_disabled_steps(dv, scope):
    steps = your_steps(dv, scope, "statecode eq 1")
    if not steps:
        return []
    return [{
        "id": "PLUGIN-02", "severity": "Low",
        "title": f"{len(steps)} custom plug-in step(s) are disabled",
        "detail": "\n".join("- " + s["name"] for s in steps[:25]),
        "fix": "If a step is no longer needed, remove it from the solution instead of leaving it disabled.",
    }]


def check_assembly_isolation(dv, scope):
    bad = [a for a in scope.assemblies if a.get("isolationmode") != 2]
    if not bad:
        return []
    return [{
        "id": "PLUGIN-03", "severity": "High",
        "title": f"{len(bad)} plug-in assembly(ies) are not registered in the sandbox",
        "detail": "\n".join("- " + a["name"] for a in bad),
        "fix": "Register custom assemblies in sandbox isolation mode.",
    }]


def check_org_settings(dv, scope):
    org = dv.get("organizations?$select=isauditenabled,plugintracelogsetting")["value"][0]
    out = []
    if not org.get("isauditenabled"):
        out.append({
            "id": "AUDIT-01", "severity": "High",
            "title": "Auditing is turned off for the whole environment",
            "detail": "No changes are being recorded, so nobody can see who changed what.",
            "fix": "Turn on auditing in the Power Platform admin center, then enable it on the tables that matter.",
        })
    if org.get("plugintracelogsetting") == 2:
        out.append({
            "id": "TRACE-01", "severity": "Medium",
            "title": "Plug-in trace log is set to 'All'",
            "detail": "Every plug-in run writes a trace row. This uses storage and adds load if left on in production.",
            "fix": "Use 'Exception' in production, and switch to 'All' only while debugging.",
        })
    return out


def check_custom_table_audit(dv, scope):
    org = dv.get("organizations?$select=isauditenabled")["value"][0]
    if not org.get("isauditenabled"):
        return []  # already reported by AUDIT-01
    ents = dv.all("EntityDefinitions?$select=LogicalName,IsAuditEnabled&$filter=IsCustomEntity eq true")
    off = [e["LogicalName"] for e in ents
           if scope.is_mine(e["LogicalName"]) and not (e.get("IsAuditEnabled") or {}).get("Value")]
    if not off:
        return []
    return [{
        "id": "AUDIT-02", "severity": "Low",
        "title": f"{len(off)} custom table(s) have auditing turned off",
        "detail": "\n".join("- " + n for n in off[:40]),
        "fix": "Turn on auditing for tables that hold business or personal data.",
    }]


def check_failed_jobs(dv, scope):
    since = (dt.datetime.utcnow() - dt.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = dv.get("asyncoperations?$select=name"
                  f"&$filter=statuscode eq 31 and createdon ge {since} and not startswith(name,'Microsoft')"
                  "&$count=true&$top=20")
    count = data.get("@odata.count", len(data.get("value", [])))
    if not count:
        return []
    names = sorted({v.get("name") or "?" for v in data.get("value", [])})
    return [{
        "id": "JOBS-01", "severity": "High" if count > 100 else ("Medium" if count > 20 else "Low"),
        "title": f"{count} system job(s) failed in the last 7 days",
        "detail": "Examples:\n" + "\n".join("- " + n for n in names),
        "fix": "Open Settings > System Jobs, filter on Failed, and fix the most common error first. "
               "Some failures are platform jobs (for example after an import); check before acting.",
    }]


def check_realtime_workflows(dv, scope):
    wf = dv.all("workflows?$select=name,primaryentity,ismanaged"
                "&$filter=category eq 0 and mode eq 1 and type eq 1 and statecode eq 1")
    wf = [w for w in wf if scope.is_mine(w.get("primaryentity")) or not w.get("ismanaged")]
    if not wf:
        return []
    return [{
        "id": "LOGIC-01", "severity": "Medium",
        "title": f"{len(wf)} active real-time (classic) workflow(s)",
        "detail": "\n".join(f"- {w['name']} ({w.get('primaryentity')})" for w in wf[:25]),
        "fix": "Real-time workflows run inside the save and are harder to maintain. Move them to plug-ins or Power Automate.",
    }]


def check_unmanaged_solutions(dv, scope):
    sols = dv.all("solutions?$select=uniquename&$filter=isvisible eq true and ismanaged eq false")
    names = [s["uniquename"] for s in sols if s["uniquename"] not in ("Default", "Active", "Basic")]
    if not names:
        return []
    return [{
        "id": "ALM-01", "severity": "Info",
        "title": f"{len(names)} unmanaged solution(s) in this environment",
        "detail": "\n".join("- " + n for n in names) +
                  "\nThis is normal in a development environment, but a warning sign in test or production.",
        "fix": "In test and production, import managed solutions built by a pipeline.",
    }]


def check_system_admins(dv, scope):
    roles = dv.all("roles?$select=name&$filter=name eq 'System Administrator'"
                   "&$expand=systemuserroles_association($select=fullname,isdisabled,accessmode)")
    users = {}
    for r in roles:
        for u in r.get("systemuserroles_association", []):
            if not u.get("isdisabled") and u.get("accessmode") == 0:  # 0 = Read-Write (real person)
                users[u["fullname"]] = True
    if len(users) <= 5:
        return []
    return [{
        "id": "SEC-01", "severity": "Medium",
        "title": f"{len(users)} active users have the System Administrator role",
        "detail": "\n".join("- " + n for n in sorted(users)[:40]),
        "fix": "Keep System Administrator for a small admin group. Give everyone else a role that matches their job.",
    }]


def check_plugin_exceptions(dv, scope):
    data = dv.get("plugintracelogs?$select=typename&$filter=exceptiondetails ne null&$count=true&$top=10")
    count = data.get("@odata.count", 0)
    if not count:
        return []
    types = sorted({v.get("typename") or "?" for v in data.get("value", [])})
    return [{
        "id": "PLUGIN-04", "severity": "Medium",
        "title": f"{count} plug-in trace log(s) contain exceptions",
        "detail": "Plug-in classes seen:\n" + "\n".join("- " + t for t in types),
        "fix": "Open Plug-in Trace Log, group by type name, and fix the class with the most errors first.",
    }]


CHECKS = [
    check_sync_update_without_filter,
    check_disabled_steps,
    check_assembly_isolation,
    check_plugin_exceptions,
    check_org_settings,
    check_custom_table_audit,
    check_failed_jobs,
    check_realtime_workflows,
    check_system_admins,
    check_unmanaged_solutions,
]


# ---------------------------------------------------------------- report
def run(org_url: str, token: str, prefixes=None):
    dv = Dataverse(org_url, token)
    scope = Scope(dv, prefixes)
    findings, errors = [], []
    for check in CHECKS:
        try:
            findings += check(dv, scope)
        except Exception as e:  # one failing check must not stop the scan
            errors.append(f"{check.__name__}: {e}")
    score = max(0, 100 - sum(SEVERITY_POINTS[f["severity"]] for f in findings))
    return findings, errors, score, scope


def to_markdown(org_url, findings, errors, score, scope=None):
    order = ["High", "Medium", "Low", "Info"]
    findings = sorted(findings, key=lambda f: order.index(f["severity"]))
    now = dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    md = [f"# Dataverse Health Check", "", f"**Environment:** {org_url}  ", f"**Scanned:** {now}  ",
          f"**Score:** {score} / 100  "]
    if scope:
        md += [f"**Your publisher prefixes:** {', '.join(scope.prefixes) or 'none found'}  ",
               f"**Your plug-in assemblies:** {len(scope.assemblies)}"]
    md += [""]
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in order}
    md += ["| High | Medium | Low | Info |", "| --- | --- | --- | --- |",
           f"| {counts['High']} | {counts['Medium']} | {counts['Low']} | {counts['Info']} |", ""]
    if not findings:
        md.append("No issues found by the checks in this version.")
    for f in findings:
        md += [f"## [{f['severity']}] {f['id']}: {f['title']}", "", f["detail"], "", f"**How to fix:** {f['fix']}", ""]
    if errors:
        md += ["## Checks that could not run", ""] + [f"- {e}" for e in errors] + [""]
    md += ["---", "Read-only scan: this tool only runs GET requests and never changes your environment."]
    return "\n".join(md)


def main():
    p = argparse.ArgumentParser(description="Read-only health check for a Dataverse environment.")
    p.add_argument("org_url", help="e.g. https://yourorg.crm.dynamics.com")
    p.add_argument("--out", default="dataverse-health-report.md", help="Markdown report file")
    p.add_argument("--prefix", action="append",
                   help="Your publisher prefix, e.g. --prefix lh (repeatable). Default: detected automatically")
    a = p.parse_args()
    token = get_token(a.org_url)
    findings, errors, score, scope = run(a.org_url, token, a.prefix)
    report = to_markdown(a.org_url, findings, errors, score, scope)
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write(report)
    print(f"Score: {score}/100, {len(findings)} finding(s). Report: {a.out}")
    if errors:
        print(f"{len(errors)} check(s) could not run; see the report.")


if __name__ == "__main__":
    main()
