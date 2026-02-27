"""Service for loading and querying CWE-to-compliance-framework mappings."""

import json
from pathlib import Path

_MAPPINGS_PATH = Path(__file__).parent.parent / "data" / "compliance_mappings.json"
_mappings: dict | None = None


def _load_mappings() -> dict:
    global _mappings
    if _mappings is None:
        with open(_MAPPINGS_PATH) as f:
            _mappings = json.load(f)
    return _mappings


def get_owasp_category(cwe_id: str) -> dict | None:
    """Look up the OWASP Top 10 2021 category for a CWE ID.

    Args:
        cwe_id: e.g. "CWE-89" or "89"

    Returns:
        {"id": "A03:2021", "name": "Injection"} or None
    """
    m = _load_mappings()
    key = cwe_id if cwe_id.startswith("CWE-") else f"CWE-{cwe_id}"
    return m["cwe_to_owasp"].get(key)


def get_pci_dss_requirement(cwe_id: str) -> dict | None:
    """Look up the PCI-DSS requirement for a CWE ID.

    Returns:
        {"req": "6.5.1", "name": "Injection Flaws"} or None
    """
    m = _load_mappings()
    key = cwe_id if cwe_id.startswith("CWE-") else f"CWE-{cwe_id}"
    return m["cwe_to_pci_dss"].get(key)


def get_soc2_control(owasp_id: str) -> dict | None:
    """Look up the SOC 2 control for an OWASP Top 10 category.

    Args:
        owasp_id: e.g. "A03:2021"

    Returns:
        {"control": "CC6.1", "name": "Logical and Physical Access Controls"} or None
    """
    m = _load_mappings()
    return m["owasp_to_soc2"].get(owasp_id)


def get_hipaa_section(owasp_id: str) -> dict | None:
    """Look up the HIPAA section for an OWASP Top 10 category.

    Returns:
        {"section": "164.312(a)(1)", "name": "Access Control"} or None
    """
    m = _load_mappings()
    return m["owasp_to_hipaa"].get(owasp_id)


def get_all_frameworks_for_cwe(cwe_id: str) -> dict:
    """Get all compliance framework mappings for a single CWE.

    Returns a dict with keys: owasp, pci_dss, soc2, hipaa (each may be None).
    """
    owasp = get_owasp_category(cwe_id)
    pci = get_pci_dss_requirement(cwe_id)
    soc2 = get_soc2_control(owasp["id"]) if owasp else None
    hipaa = get_hipaa_section(owasp["id"]) if owasp else None
    return {
        "owasp": owasp,
        "pci_dss": pci,
        "soc2": soc2,
        "hipaa": hipaa,
    }


def parse_cwe_ids_from_tags(tags: list[str]) -> list[str]:
    """Extract CWE IDs from CodeQL rule tags.

    CodeQL tags look like: ["security", "external/cwe/cwe-089", "external/cwe/cwe-564"]
    Returns: ["CWE-89", "CWE-564"]
    """
    cwe_ids = []
    for tag in tags:
        if tag.startswith("external/cwe/cwe-"):
            raw_id = tag.split("cwe-")[-1]
            # Remove leading zeros: "089" -> "89"
            cwe_ids.append(f"CWE-{int(raw_id)}")
    return cwe_ids
