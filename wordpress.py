"""
wordpress.py — WordPress/WooCommerce Maintenance Module for OpenClaw

Replaces the quarterly agency maintenance cycle with automated:
- Site health checks on all critical WooCommerce pages
- Plugin update detection and automated updates (via WP-CLI over SSH)
- Security scanning (WPScan API + wp-config checks)
- New page creation via REST API with AI-drafted content
- AI-generated maintenance report (Claude Opus)
"""

import json
import logging
import os
import shlex
import subprocess
from dataclasses import dataclass
from datetime import datetime

import httpx
import anthropic
from models import get_model

logger = logging.getLogger(__name__)

# WooCommerce critical pages to health-check after every update
CRITICAL_PAGES = ["/", "/shop/", "/cart/", "/checkout/", "/my-account/"]

# Phrases that indicate a PHP/WordPress crash in the page body
CRASH_PHRASES = [
    "there has been a critical error",
    "fatal error",
    "parse error",
    "database error",
    "call to undefined function",
    "syntax error",
]


@dataclass
class WPConfig:
    url: str           # e.g. https://example.com
    username: str      # WordPress admin username
    app_password: str  # WordPress Application Password (Settings > Users > Application Passwords)
    ssh_host: str = "" # SSH host for WP-CLI (e.g. example.com or 1.2.3.4)
    ssh_user: str = "" # SSH username on the server
    ssh_key_path: str = ""  # Path to SSH private key (~/.ssh/id_rsa)
    wpscan_token: str = ""  # WPScan API token (https://wpscan.com/api — free tier available)
    wpcli_path: str = "wp"  # Path to WP-CLI on the server (default: wp)


def load_wp_config() -> WPConfig:
    return WPConfig(
        url=os.getenv("WP_URL", "").rstrip("/"),
        username=os.getenv("WP_USERNAME", ""),
        app_password=os.getenv("WP_APP_PASSWORD", ""),
        ssh_host=os.getenv("WP_SSH_HOST", ""),
        ssh_user=os.getenv("WP_SSH_USER", ""),
        ssh_key_path=os.getenv("WP_SSH_KEY_PATH", ""),
        wpscan_token=os.getenv("WPSCAN_API_TOKEN", ""),
        wpcli_path=os.getenv("WP_CLI_PATH", "wp"),
    )


# ── Internal helpers ───────────────────────────────────────────────────────────

def _run_wpcli(config: WPConfig, args: list[str]) -> tuple[bool, str]:
    """
    Run a WP-CLI command via SSH (if configured) or locally.
    Returns (success, output_or_error_string).
    """
    cmd_parts = [config.wpcli_path] + args + ["--allow-root"]

    if config.ssh_host and config.ssh_user:
        ssh_cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=30"]
        if config.ssh_key_path:
            ssh_cmd += ["-i", config.ssh_key_path]
        ssh_cmd.append(f"{config.ssh_user}@{config.ssh_host}")
        ssh_cmd.append(shlex.join(cmd_parts))  # remote command as a single shell string
        cmd = ssh_cmd
    else:
        cmd = cmd_parts

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode == 0:
            return True, result.stdout.strip()
        return False, result.stderr.strip() or result.stdout.strip()
    except subprocess.TimeoutExpired:
        return False, "WP-CLI command timed out after 3 minutes"
    except FileNotFoundError:
        return False, f"wp-cli not found at '{config.wpcli_path}' — install WP-CLI or set WP_CLI_PATH"
    except Exception as e:
        return False, str(e)


def _wp_rest(config: WPConfig, method: str, path: str, **kwargs) -> httpx.Response:
    """Authenticated request to the WordPress REST API."""
    with httpx.Client(timeout=20, follow_redirects=True) as client:
        return client.request(
            method,
            f"{config.url}/wp-json/wp/v2/{path}",
            auth=(config.username, config.app_password),
            **kwargs,
        )


# ── Site health checks ─────────────────────────────────────────────────────────

def check_site_health(config: WPConfig) -> dict:
    """
    HTTP health check on every critical WooCommerce page.
    Detects non-200 responses and PHP/DB crash messages in the page body.
    """
    results: dict[str, dict] = {}
    issues: list[str] = []

    with httpx.Client(timeout=15, follow_redirects=True) as client:
        for path in CRITICAL_PAGES:
            url = config.url + path
            try:
                resp = client.get(url)
                body_sample = resp.text[:5000].lower()
                has_crash = any(phrase in body_sample for phrase in CRASH_PHRASES)

                results[path] = {
                    "status": resp.status_code,
                    "ok": resp.status_code == 200 and not has_crash,
                    "error": "PHP/DB critical error detected in page body" if has_crash else None,
                }
                if resp.status_code != 200:
                    issues.append(f"{path} returned HTTP {resp.status_code}")
                elif has_crash:
                    issues.append(f"{path} shows a PHP/DB critical error")

            except httpx.ConnectError:
                results[path] = {"status": 0, "ok": False, "error": "connection refused"}
                issues.append(f"{path} is unreachable")
            except httpx.TimeoutException:
                results[path] = {"status": 0, "ok": False, "error": "request timed out"}
                issues.append(f"{path} timed out")

    return {
        "pages": results,
        "issues": issues,
        "healthy": len(issues) == 0,
        "checked_at": datetime.now().isoformat(),
    }


def format_health_summary(health: dict) -> str:
    """Format check_site_health() result as a short Telegram message."""
    lines = []
    for path, info in health["pages"].items():
        if info["ok"]:
            lines.append(f"\u2705 {path}")
        else:
            err = info.get("error") or f"HTTP {info['status']}"
            lines.append(f"\u274c {path} \u2014 {err}")

    verdict = "All pages healthy." if health["healthy"] else f"{len(health['issues'])} issue(s) found."
    return "*Site Health Check*\n\n" + "\n".join(lines) + f"\n\n_{verdict}_"


# ── Plugin management ──────────────────────────────────────────────────────────

def get_wp_version(config: WPConfig) -> str:
    """Return the installed WordPress core version string."""
    ok, output = _run_wpcli(config, ["core", "version"])
    if ok and output:
        return output.strip()
    # Fallback: infer from REST API root (less reliable)
    try:
        with httpx.Client(timeout=10, follow_redirects=True) as client:
            resp = client.get(f"{config.url}/wp-json/")
            if resp.status_code == 200:
                # version not directly in root JSON — return placeholder
                return resp.json().get("description", "Unknown")
    except Exception:
        pass
    return "Unknown"


def get_plugin_updates(config: WPConfig) -> dict:
    """
    List plugins that have updates available via WP-CLI.
    Returns structured info including plugin names, current and new versions.
    """
    ok, output = _run_wpcli(config, ["plugin", "list", "--update=available", "--format=json"])
    if ok:
        try:
            plugins = json.loads(output) if output else []
            return {"method": "wpcli", "count": len(plugins), "plugins": plugins, "error": None}
        except json.JSONDecodeError:
            pass

    # WP-CLI available but JSON parsing failed — surface raw output
    return {"method": "wpcli", "count": None, "plugins": [], "error": output or "No output from WP-CLI"}


def update_all_plugins(config: WPConfig) -> dict:
    """
    Update all plugins via WP-CLI.
    Returns count of updated plugins and raw output for the AI report.
    """
    ok, output = _run_wpcli(config, ["plugin", "update", "--all", "--format=json"])
    if ok:
        try:
            updated = json.loads(output) if output.startswith("[") else []
            return {"success": True, "count": len(updated), "plugins": updated, "raw": output}
        except json.JSONDecodeError:
            # WP-CLI may output a non-JSON success message
            return {"success": True, "count": None, "plugins": [], "raw": output}
    return {"success": False, "count": 0, "plugins": [], "error": output}


# ── Security scanning ──────────────────────────────────────────────────────────

def check_security(config: WPConfig) -> dict:
    """
    Run security checks:
    1. WPScan API — known CVEs for the installed WordPress version
    2. Default 'admin' username check
    3. WP_DEBUG left on in production
    4. DISALLOW_FILE_EDIT not set (allows code editing via admin panel)
    """
    findings: list[str] = []
    wp_version = get_wp_version(config)

    # ── WPScan API vulnerability lookup ──
    wpscan: dict = {}
    if config.wpscan_token and wp_version not in ("Unknown", ""):
        try:
            version_key = wp_version.split("-")[0].replace(".", "")  # "6.4.2" → "642"
            with httpx.Client(timeout=20) as client:
                resp = client.get(
                    f"https://wpscan.com/api/v3/wordpresses/{version_key}",
                    headers={"Authorization": f"Token token={config.wpscan_token}"},
                )
            if resp.status_code == 200:
                data = resp.json()
                clean_ver = wp_version.split("-")[0]
                vulns = data.get(clean_ver, {}).get("vulnerabilities", [])
                wpscan = {
                    "vulnerabilities": len(vulns),
                    "titles": [v.get("title", "") for v in vulns[:5]],
                }
                if vulns:
                    findings.append(
                        f"WordPress {wp_version} has {len(vulns)} known vulnerabilities "
                        f"in the WPScan database — update WordPress core immediately"
                    )
            elif resp.status_code == 404:
                wpscan = {"note": f"No WPScan data for WordPress {wp_version}"}
            else:
                wpscan = {"error": f"WPScan API returned HTTP {resp.status_code}"}
        except Exception as e:
            logger.warning(f"WPScan API error: {e}")
            wpscan = {"error": str(e)}

    # ── Default 'admin' username ──
    ok, _ = _run_wpcli(config, ["user", "get", "admin", "--format=json"])
    if ok:
        findings.append(
            "Default 'admin' username exists — rename it to reduce brute-force attack risk"
        )

    # ── WP_DEBUG on in production ──
    ok, output = _run_wpcli(config, ["config", "get", "WP_DEBUG"])
    if ok and output.strip().lower() in ("true", "1"):
        findings.append(
            "WP_DEBUG is ON — disable it to prevent PHP error details leaking to visitors"
        )

    # ── DISALLOW_FILE_EDIT not set ──
    ok, output = _run_wpcli(config, ["config", "get", "DISALLOW_FILE_EDIT"])
    file_edit_disabled = ok and output.strip().lower() in ("true", "1")
    if not file_edit_disabled:
        findings.append(
            "DISALLOW_FILE_EDIT is not enabled — add it to wp-config.php to block "
            "theme/plugin code editing via the admin panel"
        )

    return {
        "wp_version": wp_version,
        "wpscan": wpscan,
        "findings": findings,
        "secure": len(findings) == 0,
        "checked_at": datetime.now().isoformat(),
    }


# ── Uptime monitoring ─────────────────────────────────────────────────────────

def is_site_up(config: WPConfig) -> tuple[bool, int]:
    """
    Single lightweight HTTP check against the homepage.
    Returns (is_up, http_status_code).
    status 0  = connection refused
    status -1 = timeout
    """
    try:
        with httpx.Client(timeout=10, follow_redirects=True) as client:
            resp = client.get(config.url + "/")
            return resp.status_code == 200, resp.status_code
    except httpx.ConnectError:
        return False, 0
    except httpx.TimeoutException:
        return False, -1
    except Exception:
        return False, -2


# ── Backup verification ────────────────────────────────────────────────────────

def check_last_backup(config: WPConfig, max_age_days: int = 7) -> dict:
    """
    Check when the site was last backed up.

    Detection order:
    1. UpdraftPlus  — reads updraft_last_backup_time from wp_options via WP-CLI
    2. BackWPup     — reads backwpup_last_backup option
    3. File search  — looks for recent .zip files in wp-content/updraft or
                      wp-content/backups via SSH (requires SSH config)

    Returns a dict with keys: plugin, last_backup (ISO str), age_days, ok, message.
    """
    # ── UpdraftPlus ──
    ok, output = _run_wpcli(config, ["option", "get", "updraft_last_backup_time"])
    if ok and output.strip().isdigit():
        ts = int(output.strip())
        if ts > 0:
            last = datetime.fromtimestamp(ts)
            age = (datetime.now() - last).days
            return {
                "plugin": "UpdraftPlus",
                "last_backup": last.strftime("%d %b %Y %H:%M"),
                "age_days": age,
                "ok": age <= max_age_days,
                "message": (
                    f"Last backup {age} day(s) ago"
                    if age <= max_age_days
                    else f"No backup in {age} days \u2014 overdue"
                ),
            }

    # ── BackWPup ──
    ok, output = _run_wpcli(config, ["option", "get", "backwpup_last_backup"])
    if ok and output.strip().isdigit():
        ts = int(output.strip())
        if ts > 0:
            last = datetime.fromtimestamp(ts)
            age = (datetime.now() - last).days
            return {
                "plugin": "BackWPup",
                "last_backup": last.strftime("%d %b %Y %H:%M"),
                "age_days": age,
                "ok": age <= max_age_days,
                "message": (
                    f"Last backup {age} day(s) ago"
                    if age <= max_age_days
                    else f"No backup in {age} days \u2014 overdue"
                ),
            }

    # ── File-based fallback (SSH required) ──
    if config.ssh_host and config.ssh_user:
        ssh_cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=30"]
        if config.ssh_key_path:
            ssh_cmd += ["-i", config.ssh_key_path]
        ssh_cmd.append(f"{config.ssh_user}@{config.ssh_host}")
        ssh_cmd.append(
            f"find ~/public_html/wp-content/updraft "
            f"~/public_html/wp-content/backups "
            f"~/public_html/wp-content/backup-db "
            f"-name '*.zip' -o -name '*.gz' 2>/dev/null "
            f"| xargs ls -t 2>/dev/null | head -1"
        )
        try:
            result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0 and result.stdout.strip():
                newest_file = result.stdout.strip()
                # Get mtime of that file
                stat_cmd = ssh_cmd[:-1] + [f"stat -c %Y {shlex.quote(newest_file)} 2>/dev/null"]
                stat_result = subprocess.run(stat_cmd, capture_output=True, text=True, timeout=15)
                if stat_result.returncode == 0 and stat_result.stdout.strip().isdigit():
                    ts = int(stat_result.stdout.strip())
                    last = datetime.fromtimestamp(ts)
                    age = (datetime.now() - last).days
                    return {
                        "plugin": "file-based",
                        "last_backup": last.strftime("%d %b %Y %H:%M"),
                        "age_days": age,
                        "ok": age <= max_age_days,
                        "message": (
                            f"Backup file found, {age} day(s) old"
                            if age <= max_age_days
                            else f"Newest backup file is {age} days old \u2014 overdue"
                        ),
                    }
        except Exception as e:
            logger.warning(f"Backup file search failed: {e}")

    return {
        "plugin": "unknown",
        "last_backup": None,
        "age_days": None,
        "ok": False,
        "message": (
            "Could not detect backup status. "
            "Install UpdraftPlus and configure SSH to enable backup monitoring."
        ),
    }


# ── Content management ─────────────────────────────────────────────────────────

def draft_page_content(title: str, description: str) -> str:
    """Use Claude (Sonnet) to write professional HTML content for a new WordPress page."""
    client = anthropic.Anthropic(api_key=os.getenv("CLAUDE_API_KEY"))
    message = client.messages.create(
        model=get_model("drafter"),
        max_tokens=1500,
        system=(
            "You write clean, professional WordPress page content. "
            "Output only HTML using h2, h3, p, ul, and li tags. "
            "No <html>, <head>, or <body> tags. No markdown. No commentary."
        ),
        messages=[{
            "role": "user",
            "content": (
                f"Write conversion-focused content for this page.\n"
                f"Title: {title}\n"
                f"Brief: {description}"
            ),
        }],
    )
    return message.content[0].text


def create_page(config: WPConfig, title: str, content: str, status: str = "draft") -> dict:
    """
    Create a WordPress page via REST API.
    Requires Application Password auth. Pages are created as drafts by default.
    """
    try:
        resp = _wp_rest(config, "POST", "pages", json={
            "title": title,
            "content": content,
            "status": status,
        })
        if resp.status_code == 201:
            data = resp.json()
            return {
                "success": True,
                "id": data["id"],
                "url": data.get("link", ""),
                "edit_url": f"{config.url}/wp-admin/post.php?post={data['id']}&action=edit",
                "status": status,
            }
        return {
            "success": False,
            "error": resp.text[:500],
            "http_status": resp.status_code,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── AI maintenance report ──────────────────────────────────────────────────────

def generate_maintenance_report(health: dict, plugins: dict, security: dict) -> str:
    """
    Use Claude (Opus) to synthesise all maintenance data into a clear owner-facing report.
    """
    client = anthropic.Anthropic(api_key=os.getenv("CLAUDE_API_KEY"))

    data_json = json.dumps(
        {"health": health, "plugins": plugins, "security": security},
        indent=2, default=str,
    )

    message = client.messages.create(
        model=get_model("orchestrator"),
        max_tokens=1200,
        system=(
            "You are a WordPress/WooCommerce maintenance specialist writing a report "
            "for a non-technical business owner. Be concise, clear, and direct."
        ),
        messages=[{
            "role": "user",
            "content": (
                "Review this maintenance data and write a structured report.\n\n"
                "Include these sections (skip any with nothing to report):\n"
                "1. OVERALL STATUS — one-line verdict\n"
                "2. SITE HEALTH — which pages are up or down\n"
                "3. PLUGIN UPDATES — what was updated or is pending\n"
                "4. SECURITY — vulnerabilities or misconfigurations found\n"
                "5. ACTION ITEMS — numbered list, most urgent first\n\n"
                f"Data:\n{data_json}"
            ),
        }],
    )
    return message.content[0].text


# ── Setup and onboarding ──────────────────────────────────────────────────────

# Known plugin categories by slug — used to flag missing essentials
_BACKUP_SLUGS = {"updraftplus", "backwpup", "duplicator", "all-in-one-wp-migration", "blogvault", "wp-staging"}
_SECURITY_SLUGS = {"wordfence", "better-wp-security", "sucuri-scanner", "wp-cerber", "all-in-one-wp-security-and-firewall", "shield-security"}
_REMOVE_SLUGS = {"hello-dolly"}  # default WordPress filler plugins with no purpose


def _plugin_name(p: dict) -> str:
    """Best available display name for a plugin dict (WP-CLI or REST API)."""
    return p.get("title") or p.get("name") or p.get("plugin", "").split("/")[0]


def _plugin_slug(p: dict) -> str:
    """Normalised slug for category lookups."""
    raw = p.get("name") or p.get("plugin", "")
    return raw.split("/")[0].lower()


def get_all_plugins(config: WPConfig) -> dict:
    """
    Fetch the complete plugin list — all statuses, with update info.

    Primary:  WP-CLI (full data — name, version, status, update availability)
    Fallback: WordPress REST API (two calls: active + inactive; no update info)
    """
    # ── WP-CLI ──
    ok, output = _run_wpcli(config, [
        "plugin", "list", "--format=json",
        "--fields=name,title,status,version,update,update_version",
    ])
    if ok and output:
        try:
            plugins = json.loads(output)
            return {
                "method": "wpcli",
                "total": len(plugins),
                "active": [p for p in plugins if p.get("status") == "active"],
                "inactive": [p for p in plugins if p.get("status") == "inactive"],
                "updates": [p for p in plugins if p.get("update") == "available"],
                "all": plugins,
                "error": None,
            }
        except json.JSONDecodeError:
            pass

    # ── REST API fallback ──
    try:
        all_plugins: list[dict] = []
        for status in ("active", "inactive"):
            resp = _wp_rest(config, "GET", "plugins", params={"per_page": 100, "status": status})
            if resp.status_code == 200:
                all_plugins.extend(resp.json())
            elif resp.status_code == 401:
                return {
                    "method": "rest_api", "total": 0, "active": [], "inactive": [],
                    "updates": [], "all": [],
                    "error": "REST API auth failed — check WP_USERNAME and WP_APP_PASSWORD",
                }
        return {
            "method": "rest_api",
            "total": len(all_plugins),
            "active": [p for p in all_plugins if p.get("status") == "active"],
            "inactive": [p for p in all_plugins if p.get("status") == "inactive"],
            "updates": [],  # REST API does not expose update availability
            "all": all_plugins,
            "note": "Update status unavailable via REST API — configure SSH+WP-CLI for full info",
            "error": None,
        }
    except Exception as e:
        return {
            "method": "none", "total": 0, "active": [], "inactive": [],
            "updates": [], "all": [],
            "error": str(e),
        }


def test_connections(config: WPConfig) -> dict:
    """
    Test each connection method (REST API, SSH+WP-CLI, WPScan API).
    Returns a dict of results for each, used by the setup report.
    """
    results: dict[str, dict] = {}

    # ── REST API ──
    if config.url and config.username and config.app_password:
        try:
            with httpx.Client(timeout=10, follow_redirects=True) as client:
                resp = client.get(
                    f"{config.url}/wp-json/wp/v2/users/me",
                    auth=(config.username, config.app_password),
                )
            if resp.status_code == 200:
                name = resp.json().get("name", config.username)
                results["rest_api"] = {"ok": True, "message": f"Connected as {name}"}
            elif resp.status_code == 401:
                results["rest_api"] = {"ok": False, "message": "Auth failed — check WP_USERNAME / WP_APP_PASSWORD"}
            elif resp.status_code == 403:
                results["rest_api"] = {"ok": False, "message": "Access denied — user needs Administrator role"}
            else:
                results["rest_api"] = {"ok": False, "message": f"HTTP {resp.status_code}"}
        except httpx.ConnectError:
            results["rest_api"] = {"ok": False, "message": f"Cannot reach {config.url} — check WP_URL"}
        except Exception as e:
            results["rest_api"] = {"ok": False, "message": str(e)}
    else:
        missing = [k for k, v in {"WP_URL": config.url, "WP_USERNAME": config.username, "WP_APP_PASSWORD": config.app_password}.items() if not v]
        results["rest_api"] = {"ok": False, "message": f"Not configured — missing: {', '.join(missing)}", "missing_config": True}

    # ── SSH + WP-CLI ──
    if config.ssh_host and config.ssh_user:
        ok, output = _run_wpcli(config, ["core", "version"])
        if ok:
            results["wpcli"] = {"ok": True, "message": f"WordPress {output.strip()}"}
        else:
            results["wpcli"] = {"ok": False, "message": output}
    else:
        results["wpcli"] = {
            "ok": False,
            "message": "Not configured — add WP_SSH_HOST + WP_SSH_USER to .env",
            "missing_config": True,
        }

    # ── WPScan API ──
    if config.wpscan_token:
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.get(
                    "https://wpscan.com/api/v3/status",
                    headers={"Authorization": f"Token token={config.wpscan_token}"},
                )
            if resp.status_code == 200:
                data = resp.json()
                requests_remaining = data.get("requests_remaining_30_days", "?")
                results["wpscan"] = {"ok": True, "message": f"Valid — {requests_remaining} requests remaining"}
            else:
                results["wpscan"] = {"ok": False, "message": f"Token rejected (HTTP {resp.status_code})"}
        except Exception as e:
            results["wpscan"] = {"ok": False, "message": str(e)}
    else:
        results["wpscan"] = {
            "ok": False,
            "message": "Not configured — add WPSCAN_API_TOKEN to .env",
            "missing_config": True,
        }

    return results


def run_setup_check() -> str:
    """
    First-run setup check. Tests all connections, audits every installed plugin,
    checks security config, verifies backups, and returns a Telegram-ready
    report with clear action items and any missing configuration.
    """
    config = load_wp_config()

    if not config.url:
        return (
            "*WordPress Setup Check*\n\n"
            "\u274c WP\\_URL is not set in .env\n\n"
            "Minimum required in .env to get started:\n"
            "`WP_URL=https://yoursite.com`\n"
            "`WP_USERNAME=your\\_admin`\n"
            "`WP_APP_PASSWORD=xxxx xxxx xxxx xxxx`"
        )

    lines: list[str] = ["*WordPress Setup Check*\n"]
    action_items: list[str] = []

    # ── Connections ──────────────────────────────────────────────────────────
    lines.append("*1. Connections*")
    conns = test_connections(config)

    for key, label in [("rest_api", "REST API"), ("wpcli", "SSH + WP-CLI"), ("wpscan", "WPScan API")]:
        c = conns[key]
        if c["ok"]:
            icon = "\u2705"
        elif c.get("missing_config"):
            icon = "\u26a0\ufe0f"
        else:
            icon = "\u274c"
        lines.append(f"{icon} {label} \u2014 {c['message']}")

    lines.append("")

    # ── Plugin audit ─────────────────────────────────────────────────────────
    if conns["rest_api"]["ok"] or conns["wpcli"]["ok"]:
        lines.append("*2. Plugin Audit*")
        pd = get_all_plugins(config)

        if pd["error"]:
            lines.append(f"\u274c {pd['error']}")
        else:
            lines.append(f"Found {pd['total']} plugins ({len(pd['active'])} active, {len(pd['inactive'])} inactive)")

            # Note if update info is unavailable
            if pd.get("note"):
                lines.append(f"_\u26a0\ufe0f {pd['note']}_")

            # Updates
            if pd["updates"]:
                lines.append(f"\n*Updates available ({len(pd['updates'])}):*")
                for p in pd["updates"]:
                    ver = p.get("version", "")
                    new_ver = p.get("update_version", "")
                    arrow = f" \u2192 {new_ver}" if new_ver else ""
                    lines.append(f"  \u2022 {_plugin_name(p)} {ver}{arrow}")
                action_items.append(f"Apply {len(pd['updates'])} plugin update(s) \u2014 run /wp\\_update")
            elif pd["method"] == "wpcli":
                lines.append("\u2705 All plugins up to date")

            # Inactive plugins
            inactive_slugs = [_plugin_slug(p) for p in pd["inactive"]]
            removable = [s for s in inactive_slugs if s in _REMOVE_SLUGS]
            other_inactive = [s for s in inactive_slugs if s not in _REMOVE_SLUGS]

            if removable:
                lines.append(f"\n*Delete these (default/useless plugins):*")
                for s in removable:
                    lines.append(f"  \u2022 {s}")
                action_items.append(f"Delete {len(removable)} useless plugin(s): {', '.join(removable)}")

            if other_inactive:
                lines.append(f"\n*Inactive plugins \u2014 delete if not needed ({len(other_inactive)}):*")
                for s in other_inactive[:6]:
                    lines.append(f"  \u2022 {s}")
                if len(other_inactive) > 6:
                    lines.append(f"  \u2026 and {len(other_inactive) - 6} more")
                action_items.append(f"Review and delete {len(other_inactive)} inactive plugin(s) \u2014 inactive plugins still carry security risk")

            # Essential plugin categories
            all_slugs = {_plugin_slug(p) for p in pd["all"]}
            has_backup = bool(all_slugs & _BACKUP_SLUGS)
            has_security = bool(all_slugs & _SECURITY_SLUGS)

            lines.append("\n*Essential plugins:*")
            lines.append(f"{'✅' if has_backup else '❌'} Backup plugin \u2014 {'installed' if has_backup else 'MISSING \u2014 install UpdraftPlus (free)'}")
            lines.append(f"{'✅' if has_security else '❌'} Security plugin \u2014 {'installed' if has_security else 'MISSING \u2014 install Wordfence (free)'}")

            if not has_backup:
                action_items.append("Install UpdraftPlus (free backup plugin) \u2014 required before any plugin updates")
            if not has_security:
                action_items.append("Install Wordfence (free security plugin) \u2014 monitors for malware and attacks")

        lines.append("")

    # ── Security config ───────────────────────────────────────────────────────
    if conns["wpcli"]["ok"]:
        lines.append("*3. Security Config*")
        sec = check_security(config)
        if sec["findings"]:
            for f in sec["findings"]:
                lines.append(f"\u26a0\ufe0f {f}")
            action_items.extend(sec["findings"])
        else:
            lines.append("\u2705 No issues found")
        lines.append("")

    # ── Backup status ─────────────────────────────────────────────────────────
    if conns["wpcli"]["ok"] or conns["rest_api"]["ok"]:
        lines.append("*4. Backup Status*")
        backup = check_last_backup(config)
        icon = "\u2705" if backup["ok"] else "\u274c"
        lines.append(f"{icon} {backup['message']}")
        if backup.get("plugin") and backup["plugin"] != "unknown":
            lines.append(f"  Plugin: {backup['plugin']}")
        if not backup["ok"]:
            action_items.insert(0, "Run a manual backup before applying any updates")
        lines.append("")

    # ── Missing .env config ───────────────────────────────────────────────────
    missing_env: list[str] = []
    if not config.ssh_host:
        missing_env.append("`WP_SSH_HOST` \u2014 your server's hostname or IP")
    if not config.ssh_user:
        missing_env.append("`WP_SSH_USER` \u2014 your SSH login username")
    if not config.wpscan_token:
        missing_env.append("`WPSCAN_API_TOKEN` \u2014 optional, from wpscan.com/api (free)")

    if missing_env:
        lines.append("*Still needed in .env:*")
        for m in missing_env:
            lines.append(f"\u2022 {m}")
        lines.append("")

    # ── Action items ──────────────────────────────────────────────────────────
    if action_items:
        lines.append("*Action Items:*")
        for i, item in enumerate(action_items, 1):
            lines.append(f"{i}. {item}")
    else:
        lines.append("\u2705 Everything is configured and looks good.")

    return "\n".join(lines)


# ── Orchestration ──────────────────────────────────────────────────────────────

def run_quick_health_check() -> str:
    """
    Lightweight daily check — returns a Telegram-ready message.
    Called by the daily scheduler and /wp_status command.
    """
    config = load_wp_config()
    if not config.url:
        return "WP_URL is not set in .env — WordPress monitoring is not configured."
    health = check_site_health(config)
    return format_health_summary(health)


def run_full_maintenance(apply_updates: bool = False) -> str:
    """
    Full quarterly maintenance run:
      1. Site health check
      2. Plugin update check (and apply if apply_updates=True)
      3. Post-update health re-check (if updates were applied)
      4. Security scan
      5. AI-generated report

    Returns a Telegram-ready string (may be long — caller should split it).
    """
    config = load_wp_config()
    if not config.url:
        return (
            "WordPress is not configured.\n"
            "Add WP_URL, WP_USERNAME, and WP_APP_PASSWORD to your .env file."
        )

    now = datetime.now().strftime("%d %b %Y %H:%M")
    progress_lines = [f"*WordPress Maintenance \u2014 {now}*\n"]

    # Step 1: Health check
    progress_lines.append("Step 1/4: Checking site health\u2026")
    health = check_site_health(config)

    # Step 2: Plugin updates
    progress_lines.append("Step 2/4: Checking plugin updates\u2026")
    plugins = get_plugin_updates(config)

    if apply_updates and plugins.get("count"):
        n = plugins["count"]
        progress_lines.append(f"  Updating {n} plugin(s)\u2026")
        update_result = update_all_plugins(config)
        plugins["update_result"] = update_result

        # Re-check health after updates to catch any breakage
        progress_lines.append("  Re-checking site after updates\u2026")
        health["post_update_check"] = check_site_health(config)
    elif apply_updates and plugins.get("count") == 0:
        progress_lines.append("  All plugins are up to date \u2014 nothing to update.")

    # Step 3: Security scan
    progress_lines.append("Step 3/4: Running security scan\u2026")
    security = check_security(config)

    # Step 4: AI report
    progress_lines.append("Step 4/4: Generating report\u2026\n")
    report = generate_maintenance_report(health, plugins, security)

    progress = "\n".join(progress_lines)
    return f"{progress}\n{report}"
