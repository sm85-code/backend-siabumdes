"""Organization letterhead for financial PDF/Excel/Word exports.

Historically env-var driven (get_report_branding); now editable at runtime
via the Profil BUMDES menu (OrgProfile row, see modules.siabumdes.identity) and
converted here with branding_from_org_profile(). The env-var path stays as
a seed/fallback for deployments that haven't opened that menu yet, and for
call sites (kwitansi/invoice) that don't have a DB row to read.
"""
from __future__ import annotations

import logging
import os
import urllib.request
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReportBranding:
    org_name: str
    org_legal_name: str
    address: str
    village: str
    district: str
    regency: str
    province: str
    phone: str
    email: str
    tagline: str
    signatory_left_title: str
    signatory_left_name: str
    signatory_mid_title: str
    signatory_mid_name: str
    signatory_right_title: str
    signatory_right_name: str
    primary_color: str  # hex without #
    logo_url: str = field(default="")

    @property
    def location_line(self) -> str:
        parts = [p for p in (self.village, self.district, self.regency, self.province) if p]
        return ", ".join(parts)

    @property
    def address_block(self) -> str:
        lines = [self.address, self.location_line]
        contact = " · ".join(x for x in (self.phone, self.email) if x)
        if contact:
            lines.append(contact)
        return "\n".join(x for x in lines if x)


@lru_cache(maxsize=1)
def get_report_branding() -> ReportBranding:
    return ReportBranding(
        org_name=os.getenv("REPORT_ORG_NAME", os.getenv("ORG_NAME", "BUMDes")),
        org_legal_name=os.getenv(
            "REPORT_ORG_LEGAL_NAME",
            os.getenv("ORG_LEGAL_NAME", "Badan Usaha Milik Desa"),
        ),
        address=os.getenv("REPORT_ORG_ADDRESS", os.getenv("ORG_ADDRESS", "")),
        village=os.getenv("REPORT_ORG_VILLAGE", os.getenv("ORG_VILLAGE", "")),
        district=os.getenv("REPORT_ORG_DISTRICT", os.getenv("ORG_DISTRICT", "")),
        regency=os.getenv("REPORT_ORG_REGENCY", os.getenv("ORG_REGENCY", "")),
        province=os.getenv("REPORT_ORG_PROVINCE", os.getenv("ORG_PROVINCE", "")),
        phone=os.getenv("REPORT_ORG_PHONE", os.getenv("ORG_PHONE", "")),
        email=os.getenv("REPORT_ORG_EMAIL", os.getenv("ORG_EMAIL", "")),
        tagline=os.getenv("REPORT_ORG_TAGLINE", "Laporan Keuangan"),
        signatory_left_title=os.getenv("REPORT_SIGN_LEFT_TITLE", "Direktur / Ketua"),
        signatory_left_name=os.getenv("REPORT_SIGN_LEFT_NAME", ""),
        signatory_mid_title=os.getenv("REPORT_SIGN_MID_TITLE", "Bendahara"),
        signatory_mid_name=os.getenv("REPORT_SIGN_MID_NAME", ""),
        signatory_right_title=os.getenv("REPORT_SIGN_RIGHT_TITLE", "Mengetahui"),
        signatory_right_name=os.getenv("REPORT_SIGN_RIGHT_NAME", ""),
        primary_color=os.getenv("REPORT_PRIMARY_COLOR", "1F4E79").lstrip("#"),
        logo_url=os.getenv("REPORT_ORG_LOGO_URL", ""),
    )


def branding_from_org_profile(profile) -> ReportBranding:
    """Convert an OrgProfile DB row (modules.siabumdes.identity.infrastructure.models)
    into the ReportBranding shape the PDF/Excel/Word generators expect."""
    return ReportBranding(
        org_name=profile.org_name,
        org_legal_name=profile.org_legal_name,
        address=profile.address,
        village=profile.village,
        district=profile.district,
        regency=profile.regency,
        province=profile.province,
        phone=profile.phone,
        email=profile.email,
        tagline=profile.tagline,
        signatory_left_title=profile.signatory_left_title,
        signatory_left_name=profile.signatory_left_name,
        signatory_mid_title=profile.signatory_mid_title,
        signatory_mid_name=profile.signatory_mid_name,
        signatory_right_title=profile.signatory_right_title,
        signatory_right_name=profile.signatory_right_name,
        primary_color=(profile.primary_color or "1F4E79").lstrip("#"),
        logo_url=profile.logo_url or "",
    )


def fetch_logo_bytes(url: str, *, timeout: float = 5.0) -> Optional[bytes]:
    """Best-effort fetch of the letterhead logo for formats that need raw
    image bytes (Excel, Word -- WeasyPrint fetches the URL itself). Returns
    None (never raises) so a slow/broken logo URL never breaks a report.

    Some hosts (Google Drive's own hotlink URLs included) serve an HTML
    interstitial instead of the image for a plain, cookie-less, no-UA
    request -- send a browser-like User-Agent and refuse to return anything
    whose Content-Type isn't actually an image, so that HTML page never
    gets embedded into a report as if it were the logo.
    """
    if not url:
        return None
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; BUMDesReportBot/1.0)"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310
            content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if content_type and not content_type.startswith("image/"):
                logger.warning(
                    "Logo BUMDes di %s bukan gambar (Content-Type: %s) -- kemungkinan halaman "
                    "konfirmasi, bukan file gambar langsung",
                    url,
                    content_type,
                )
                return None
            return resp.read()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gagal mengambil logo BUMDes dari %s: %s", url, exc)
        return None
