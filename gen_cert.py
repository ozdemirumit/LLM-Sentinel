"""
Self-signed TLS certificate generator.

Usage:
    python main.py --gen-cert
    # or directly:
    python gen_cert.py
"""

from __future__ import annotations

import datetime
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from logger import get_logger

log = get_logger(__name__)


def generate_self_signed_cert(
    cert_path: str = "certs/server.crt",
    key_path: str = "certs/server.key",
    days: int = 365,
    common_name: str = "onPrem LLM Sentinel",
) -> tuple[Path, Path]:
    """
    Generate a self-signed RSA 2048 X.509 certificate.

    Returns (cert_path, key_path) as Path objects.
    """
    cert_file = Path(cert_path)
    key_file = Path(key_path)

    # Create directory
    cert_file.parent.mkdir(parents=True, exist_ok=True)

    # Generate RSA 2048 key
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )

    # Build certificate
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "onPrem LLM Sentinel"),
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
    ])

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=days))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.DNSName("*.localhost"),
                x509.IPAddress(
                    __import__("ipaddress").IPv4Address("127.0.0.1")
                ),
            ]),
            critical=False,
        )
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=0),
            critical=True,
        )
        .sign(private_key, hashes.SHA256())
    )

    # Write key
    key_file.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )

    # Write cert
    cert_file.write_bytes(
        cert.public_bytes(serialization.Encoding.PEM)
    )

    log.info(
        "Self-signed certificate generated",
        extra={"cert": str(cert_file), "key": str(key_file), "days": days},
    )
    print(f"Certificate: {cert_file}")
    print(f"Private key: {key_file}")
    print(f"Valid for {days} days")

    return cert_file, key_file


if __name__ == "__main__":
    from logger import setup_logging
    setup_logging()
    generate_self_signed_cert()
