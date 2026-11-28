"""Ed25519 provenance for explicit offline workforce exchanges.

The signing seed lives in the existing local authentication directory.
A review trusts only public keys explicitly pinned by the operator.
A signature proves possession of that signing key, not real-world identity,
accuracy of telemetry, completeness of an installation, or organization roles.
"""
from __future__ import annotations

from hashlib import sha256
from typing import Annotated, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pydantic import Field, StrictBool, model_validator

from shared.agent_procurement import Alias, Strict, digest
from shared.process_shadow_review import Hash
from . import agent_procurement_workforce_exchange as exchange
from . import local_auth

SCHEME="openworkgraph.workforce-exchange-signature.v1"
KEY_FILE=".workforce_exchange_signing_seed"
Signature=Annotated[str,Field(pattern=r"^[a-f0-9]{128}$")]


class IdentityRequest(Strict):
    confirmed: StrictBool


class Trust(Strict):
    installation_alias: Alias
    public_key_hex: Hash
    correlation_namespace_sha256: Hash


class SignedPacket(Strict):
    expected_packet_sha256: Hash
    packet: exchange.Packet
    signature_hex: Signature


class SignedReview(Strict):
    expected_correlation_namespace_sha256: Hash
    maximum_packet_age_ms: Annotated[int,Field(strict=True,ge=1,le=366*24*3600000)]
    trust: list[Trust]=Field(min_length=1,max_length=exchange.MAX_SITES)
    packets: list[SignedPacket]=Field(min_length=1,max_length=exchange.MAX_SITES)
    confirmed: StrictBool

    @model_validator(mode="after")
    def unique(self):
        if len({x.installation_alias.casefold() for x in self.trust})!=len(self.trust):
            raise ValueError("duplicate trusted installation alias")
        if len({x.public_key_hex for x in self.trust})!=len(self.trust):
            raise ValueError("one signing key per trusted installation")
        if any(x.correlation_namespace_sha256!=self.expected_correlation_namespace_sha256 for x in self.trust):
            raise ValueError("trusted namespace mismatch")
        if {x.installation_alias for x in self.trust}!={x.packet.installation_alias for x in self.packets}:
            raise ValueError("exact explicit trust set required")
        return self


def _private():
    # Reuse the same local-only secret-file lifecycle as other installation
    # credentials. Domain-separated derivation never exposes the seed.
    secret=local_auth._read_or_create_secret(KEY_FILE)
    if len(secret)<48 or not secret.isascii():
        raise ValueError("invalid local signing seed")
    seed=sha256(b"owg-workforce-ed25519-v1\x00"+secret.encode("ascii")).digest()
    return Ed25519PrivateKey.from_private_bytes(seed)


def _public(key):
    return key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex()


def _message(packet_sha256):
    # The canonical packet digest commits to every packet field, including
    # alias, namespace and export timestamp. No algorithm negotiation.
    return (SCHEME+"\x00").encode("ascii")+bytes.fromhex(packet_sha256)


def sign_packet(key,packet):
    pin=digest(packet)
    return {"expected_packet_sha256":pin,"packet":packet,
            "signature_hex":key.sign(_message(pin)).hex()}


def verify_packet(public_key_hex,packet,pin,signature_hex):
    if digest(packet)!=pin:raise ValueError("packet pin mismatch")
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex)).verify(
            bytes.fromhex(signature_hex),_message(pin))
    except (InvalidSignature,ValueError,TypeError):
        raise ValueError("installation signature invalid") from None


def identity(document):
    req=IdentityRequest.model_validate(document)
    if not req.confirmed:raise ValueError("explicit signing identity consent required")
    public=_public(_private())
    return {"scheme":SCHEME,"public_key_hex":public,
            "public_key_sha256":sha256(bytes.fromhex(public)).hexdigest(),
            "private_key_exported":False,"organizational_identity_verified":False}


def signed_export(document):
    # Validate explicit export consent before creating a signing identity.
    result=exchange.export(document)
    key=_private()
    public=_public(key)
    return {"signed_packet":sign_packet(key,result["packet"]),"scheme":SCHEME,
            "public_key_hex":public,"public_key_sha256":sha256(bytes.fromhex(public)).hexdigest(),
            "source_identity_verified":False,"result_persisted":False,"execution_authorized":False}


def signed_review(document,*,now=None):
    req=SignedReview.model_validate(document)
    if not req.confirmed:raise ValueError("explicit signed review consent required")
    trusted={t.installation_alias:t for t in req.trust}
    pins=[]
    for p in req.packets:
        alias=p.packet.installation_alias
        public=trusted[alias].public_key_hex
        verify_packet(public,p.packet.model_dump(),p.expected_packet_sha256,p.signature_hex)
        pins.append({"installation_alias":alias,"public_key_sha256":sha256(bytes.fromhex(public)).hexdigest(),
                     "packet_sha256":p.expected_packet_sha256})
    # Delegate all freshness, duplicate alias, count, currency and overlap
    # checks to the unsigned exchange reviewer after signatures pass.
    result=exchange.review({"expected_correlation_namespace_sha256":req.expected_correlation_namespace_sha256,
        "maximum_packet_age_ms":req.maximum_packet_age_ms,"confirmed":True,
        "packets":[{"expected_packet_sha256":p.expected_packet_sha256,"packet":p.packet.model_dump()}
                   for p in req.packets]},now=now)
    result.pop("report_sha256")
    result["format"]="openworkgraph.workforce-signed-exchange-review.v1"
    result["all_packet_signatures_verified_against_operator_pins"]=True
    result["trusted_key_fingerprints"]=sorted(pins,key=lambda x:x["installation_alias"])
    result["source_identity_verified"]=False
    result["organizational_identity_verified"]=False
    result["persistent_replay_history_verified"]=False
    result["limitations"].append("Every packet signature verifies against the explicit operator-pinned public key. Key ownership and alias mapping must be established separately; fresh signed packets can be replayed in another offline review.")
    return {**result,"report_sha256":digest(result)}
