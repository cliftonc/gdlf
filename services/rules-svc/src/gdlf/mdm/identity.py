"""Per-device identity certificates.

Each MDM-enrolled device gets an RSA 2048 keypair signed by the gdlf MDM CA
(generated via `./gdlf mdm-ca init`). The cert + key are bundled into a
PKCS12 that's embedded directly in the device's enrollment .mobileconfig.

Apple installs the PKCS12 into the keychain on enrollment; from then on the
MDM client uses it as the TLS client identity for every check-in and command
poll. Caddy validates the chain against the MDM CA (`client_auth verify_if_given`)
and forwards the cert details to rules-svc as `X-Mdm-Client-Cert-B64`.

Cert validity is intentionally long (10y, matching the CA itself) — we have
no in-band cert rotation (no SCEP server). If a cert ever needs to be replaced
before expiry, push a new enrollment profile via the MDM command channel
(InstallProfile) while the device is still reachable.
"""
from __future__ import annotations

import datetime as dt
import secrets
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from ..settings import settings

# Validity matches the MDM CA's 10y span. The device cert can never outlive
# the CA that signed it; pinning them to the same horizon means we never have
# to think about asymmetric expiry.
CERT_VALIDITY = dt.timedelta(days=3650)


def _ca_dir() -> Path:
    return settings.state_dir / "mdm-ca"


@lru_cache(maxsize=1)
def _load_ca() -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
    """Read the gdlf MDM CA cert + key once; cached for the process lifetime.

    Cached because every enrollment mints a new cert and we don't want to
    reparse PEM on every call. Restart rules-svc if the CA is rotated."""
    cert_pem = (_ca_dir() / "ca.pem").read_bytes()
    key_pem = (_ca_dir() / "ca.key").read_bytes()
    cert = x509.load_pem_x509_certificate(cert_pem)
    key = serialization.load_pem_private_key(key_pem, password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise RuntimeError(f"unexpected MDM CA key type: {type(key).__name__}")
    return cert, key


@dataclass(frozen=True)
class MintedIdentity:
    """Result of minting a device identity cert.

    The PKCS12 is what gets embedded in the .mobileconfig; the password
    must be embedded alongside it so iOS can decrypt at install time.

    The serial is stored on the Device.mdm record so we can build a CRL or
    track which certs we've issued (useful for future revocation work).
    """
    identity_cn: str          # CN on the cert; used as the device key on mTLS
    serial_hex: str           # cert serial in hex, lowercase, no leading "0x"
    pkcs12_bytes: bytes       # DER-encoded PKCS12 — opaque blob for the profile
    pkcs12_password: str      # random; included plaintext in the profile


def mint_device_identity(*, wg_ip: str) -> MintedIdentity:
    """Mint a fresh identity cert + PKCS12 for the device at `wg_ip`.

    CN format: `gdlf-device-<wg_ip>`. The MDM checkin handlers extract the
    CN from the X-Mdm-Client-Subject header that Caddy forwards, and look
    the device up by `wg_ip` from there. No collision risk because the CA
    only signs our own cert requests.
    """
    ca_cert, ca_key = _load_ca()

    device_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cn = f"gdlf-device-{wg_ip}"
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, cn),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "gdlf"),
    ])
    now = dt.datetime.now(dt.UTC)
    # Random 128-bit serial — RFC 5280 strongly recommends large random
    # serials over sequential ones (defends against hash collision attacks
    # on weak hash functions and avoids leaking issuance volume).
    serial = x509.random_serial_number()
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(device_key.public_key())
        .serial_number(serial)
        .not_valid_before(now - dt.timedelta(minutes=5))   # tolerate small clock skew
        .not_valid_after(now + CERT_VALIDITY)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=True,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.CLIENT_AUTH]),
            critical=False,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(device_key.public_key()),
            critical=False,
        )
        .sign(private_key=ca_key, algorithm=hashes.SHA256())
    )

    # Random 16-byte hex password. Embedded plaintext in the .mobileconfig
    # next to the PKCS12 — Apple decrypts in-place during install. Not a
    # secret beyond the profile itself.
    password = secrets.token_hex(16)
    pkcs12_bytes = pkcs12.serialize_key_and_certificates(
        name=cn.encode("utf-8"),
        key=device_key,
        cert=cert,
        cas=[ca_cert],
        encryption_algorithm=serialization.BestAvailableEncryption(password.encode("utf-8")),
    )

    return MintedIdentity(
        identity_cn=cn,
        serial_hex=f"{serial:x}",
        pkcs12_bytes=pkcs12_bytes,
        pkcs12_password=password,
    )


def cn_from_subject_header(subject_header: str | None) -> str | None:
    """Pull the CN out of Caddy's X-Mdm-Client-Subject header.

    Caddy emits the subject DN in a stringified format like
    `CN=gdlf-device-10.13.13.5,O=gdlf`. We only care about the CN. Returns
    None if the header is missing or has no CN.
    """
    if not subject_header:
        return None
    for part in subject_header.split(","):
        part = part.strip()
        if part.upper().startswith("CN="):
            return part[3:]
    return None
