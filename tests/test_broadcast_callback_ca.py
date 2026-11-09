# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# All Rights Reserved.
#
# SPDX-License-Identifier: Apache-2.0

"""broadcast.callback.ca.file: extra CAs for webhook TLS, checked with a real TLS handshake."""

import asyncio
import datetime
import ipaddress
import ssl
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

import agent_registry.broadcast as broadcast
from agent_registry.broadcast import BroadcastService, callback_ssl_context
from agent_registry.broadcast.dispatcher import WebhookDispatcher
from agent_registry.broadcast.outbox import MemoryOutbox
from agent_registry.broadcast.subscriptions import MemorySubscriptionStore


def _name(cn):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _pem(cert):
    return cert.public_bytes(serialization.Encoding.PEM)


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    """A private CA and a server certificate for 127.0.0.1 signed by it."""
    tmp = tmp_path_factory.mktemp("pki")
    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = (x509.CertificateBuilder().subject_name(_name("test-callback-ca")).issuer_name(_name("test-callback-ca"))
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - datetime.timedelta(minutes=1)).not_valid_after(now + datetime.timedelta(days=1))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                       content_commitment=False, key_encipherment=False, data_encipherment=False,
                                       key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .sign(ca_key, hashes.SHA256()))
    key = ec.generate_private_key(ec.SECP256R1())
    leaf = (x509.CertificateBuilder().subject_name(_name("127.0.0.1")).issuer_name(ca.subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=1)).not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                           critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    (tmp / "ca.pem").write_bytes(_pem(ca))
    (tmp / "server.pem").write_bytes(_pem(leaf))
    (tmp / "server.key").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                       serialization.NoEncryption()))
    return tmp


@pytest.fixture(scope="module")
def https_url(pki):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(pki / "server.pem"), str(pki / "server.key"))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"https://127.0.0.1:{server.server_address[1]}/callback"
    server.shutdown()
    server.server_close()


def _post(dispatcher, url):
    async def run():
        try:
            return (await dispatcher._client.post(url, json={"probe": 1})).status_code
        finally:
            await dispatcher._client.aclose()
    return asyncio.run(run())


def _dispatcher(**kwargs):
    return WebhookDispatcher(MemorySubscriptionStore(), MemoryOutbox(), **kwargs)


def test_no_file_means_default_verification():
    assert callback_ssl_context("") is None
    assert callback_ssl_context(None) is None
    assert callback_ssl_context("   ") is None


def test_private_ca_listener_rejected_by_default(https_url):
    with pytest.raises(httpx.ConnectError):
        _post(_dispatcher(), https_url)


def test_extra_ca_lets_dispatcher_reach_private_listener(pki, https_url):
    assert _post(_dispatcher(verify=callback_ssl_context(str(pki / "ca.pem"))), https_url) == 204


def test_extra_ca_keeps_default_cas(pki):
    context = callback_ssl_context(str(pki / "ca.pem"))
    default_count = len(ssl.create_default_context(cafile=__import__("certifi").where()).get_ca_certs())
    assert len(context.get_ca_certs()) == default_count + 1
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


def test_relative_path_resolves_against_root(pki, monkeypatch):
    monkeypatch.setattr(broadcast, "get_root_path", lambda: str(pki))
    assert callback_ssl_context("ca.pem") is not None


@pytest.mark.parametrize("content", [None, b"", b"not a certificate\n"])
def test_unloadable_file_fails_closed(tmp_path, content):
    path = tmp_path / "ca.pem"
    if content is not None:
        path.write_bytes(content)
    with pytest.raises(RuntimeError, match="broadcast.callback.ca.file"):
        callback_ssl_context(str(path))


def test_service_wires_the_setting(pki, https_url):
    config = {"broadcast.enabled": "true", "broadcast.callback.ca.file": str(pki / "ca.pem")}
    service = BroadcastService(MemoryOutbox(), MemorySubscriptionStore(), config=config)
    assert _post(service.dispatcher, https_url) == 204


def test_service_refuses_to_start_with_a_bad_file(tmp_path):
    config = {"broadcast.enabled": "true", "broadcast.callback.ca.file": str(tmp_path / "missing.pem")}
    with pytest.raises(RuntimeError, match="broadcast.callback.ca.file"):
        BroadcastService(MemoryOutbox(), MemorySubscriptionStore(), config=config)


def test_setting_ignored_while_broadcast_disabled(tmp_path):
    config = {"broadcast.enabled": "false", "broadcast.callback.ca.file": str(tmp_path / "missing.pem")}
    assert BroadcastService(MemoryOutbox(), MemorySubscriptionStore(), config=config).dispatcher is None
