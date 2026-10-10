from __future__ import annotations

"""Build the public OpenWorkGraph plugin package after production URLs exist."""

import argparse
import json
import re
from pathlib import Path
import shutil
from urllib.parse import urlparse
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "integrations" / "chatgpt"


def _https(value: str, label: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise SystemExit(f"{label} must be a public HTTPS URL")
    if parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        raise SystemExit(f"{label} must not be a loopback URL")
    return value.rstrip("/")


def build(*, mcp_url: str, homepage: str, privacy_url: str, company_url: str, support_url: str, terms_url: str, demo_recording_url: str, logo: Path, countries: list[str], developer_name: str, version: str, output: Path, registered_app_id: str | None = None) -> Path:
    mcp_url = _https(mcp_url, "mcp_url")
    homepage = _https(homepage, "homepage")
    privacy_url = _https(privacy_url, "privacy_url")
    company_url = _https(company_url, "company_url")
    support_url = _https(support_url, "support_url")
    terms_url = _https(terms_url, "terms_url")
    demo_recording_url = _https(demo_recording_url, "demo_recording_url")
    if logo.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".svg"} or not logo.is_file():
        raise SystemExit("logo must be an existing PNG, JPEG, WebP, or SVG file")
    developer_name = str(developer_name or "").strip()
    if not developer_name or len(developer_name) > 80:
        raise SystemExit("developer_name must match the verified publisher identity and be at most 80 characters")
    countries = [str(x).strip().upper() for x in countries if str(x).strip()]
    if not countries or any(len(x) != 2 or not x.isalpha() for x in countries):
        raise SystemExit("countries must contain one or more two-letter country codes")
    if not urlparse(mcp_url).path.endswith("/mcp"):
        raise SystemExit("mcp_url must point to the streamable HTTP /mcp endpoint")

    package = output.parent / "openworkgraph-plugin"
    if package.exists():
        shutil.rmtree(package)
    skill_dir = package / "skills" / "openworkgraph"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        (SOURCE / "skills" / "openworkgraph" / "SKILL.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    review_cases = json.loads((SOURCE / "review_cases.json").read_text(encoding="utf-8"))
    (package / "review_cases.json").write_text(json.dumps(review_cases, indent=2) + "\n", encoding="utf-8")
    assets = package / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    logo_name = "logo" + logo.suffix.lower()
    (assets / logo_name).write_bytes(logo.read_bytes())

    manifest = {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": "openworkgraph",
        "version": version,
        "description": "Give ChatGPT evidence of how your work actually happened so it can continue, explain, improve, and help automate real workflows.",
        "author": {"name": developer_name, "url": company_url},
        "homepage": homepage,
        "repository": "https://github.com/KAVentures/openworkgraph",
        "license": "Apache-2.0",
        "keywords": ["work context", "workflow evidence", "automation", "agent continuity"],
        "extensions": {
            "com.openai": {
                "interface": {
                    "displayName": "OpenWorkGraph",
                    "shortDescription": "Work evidence for ChatGPT",
                    "longDescription": "Use privacy-hardened observed work evidence to continue recent work, understand repeated workflows, inspect prior agent attempts, and design better automations without treating history as permission.",
                    "developerName": developer_name,
                    "category": "Productivity",
                    "capabilities": ["Read"],
                    "websiteURL": homepage,
                    "privacyPolicyURL": privacy_url,
                    "termsOfServiceURL": terms_url,
                    "supportURL": support_url,
                    "defaultPrompt": [
                        "Continue what I was working on.",
                        "How did I handle this kind of work before?",
                        "Help me improve this workflow using how I actually work."
                    ],
                    "composerIcon": f"./assets/{logo_name}",
                    "logo": f"./assets/{logo_name}"
                },
                "onboardingSkill": "./skills/openworkgraph/SKILL.md",
                "review": {
                    "test_cases": review_cases,
                    "demo_recording_url": demo_recording_url,
                    "commerce": False,
                    "commerce_description": "OpenWorkGraph does not sell products or process payments."
                },
                "publication": {
                    "countries": countries,
                    "release_notes": "Initial public release: read-only observed work context, repeated-work evidence, and prior-agent continuity."
                }
            }
        }
    }
    mcp = {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
        "mcpServers": {
            "openworkgraph": {
                "type": "streamable-http",
                "url": mcp_url
            }
        }
    }
    # A registered ChatGPT app is an alternative to a bundled MCP server.
    # Keep the existing portable/direct-MCP output as the default. A mapping
    # must reference a *real* app registered for this exact OAuth endpoint in
    # the target workspace; never synthesize its identity from the MCP URL.
    if registered_app_id:
        raw = str(registered_app_id).strip()
        if not re.fullmatch(r"(?:plugin_)?asdk_app_[0-9a-fA-F]{32}", raw):
            raise SystemExit("registered_app_id must be a real plugin_asdk_app_/asdk_app_ technical ID")
        canonical_id = raw.removeprefix("plugin_")
        manifest["extensions"]["com.openai"]["apps"] = "./.app.json"
        registered = {"apps": {"openworkgraph": {"id": canonical_id, "required": True}}}
        (package / ".app.json").write_text(json.dumps(registered, indent=2) + "\n", encoding="utf-8")
    else:
        # Portable MCP package for existing Codex / ChatGPT Work connections.
        (package / "mcp.json").write_text(json.dumps(mcp, indent=2) + "\n", encoding="utf-8")
    (package / "plugin.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(package.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(package))
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mcp-url", required=True)
    parser.add_argument("--homepage", required=True)
    parser.add_argument("--privacy-url", required=True)
    parser.add_argument("--company-url", required=True)
    parser.add_argument("--support-url", required=True)
    parser.add_argument("--terms-url", required=True)
    parser.add_argument("--demo-recording-url", required=True)
    parser.add_argument("--logo", required=True, type=Path)
    parser.add_argument("--countries", required=True, help="Comma-separated two-letter country codes")
    parser.add_argument("--developer-name", required=True, help="Must match the verified publisher identity")
    parser.add_argument("--version", default="1.0.0")
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "openworkgraph-chatgpt-plugin.zip")
    parser.add_argument("--registered-app-id", help="Optional real ChatGPT registered-app ID (plugin_asdk_app_...); emits a separate .app.json variant without bundled MCP")
    args = parser.parse_args()
    print(build(
        mcp_url=args.mcp_url,
        homepage=args.homepage,
        privacy_url=args.privacy_url,
        company_url=args.company_url,
        support_url=args.support_url,
        terms_url=args.terms_url,
        demo_recording_url=args.demo_recording_url,
        logo=args.logo,
        countries=args.countries.split(","),
        developer_name=args.developer_name,
        version=args.version,
        output=args.output,
        registered_app_id=args.registered_app_id,
    ))


if __name__ == "__main__":
    main()
