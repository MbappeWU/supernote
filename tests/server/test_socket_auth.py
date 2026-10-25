from supernote.models.socket import SocketHandshakeParams
from supernote.server.socket_auth import (
    compute_handshake_signature,
    verify_handshake_signature,
)


def test_compute_and_verify_handshake_signature_valid() -> None:
    token = "test-token"
    conn_type = "file"
    random_val = "rnd123"
    sign = compute_handshake_signature(token, conn_type, random_val)
    assert sign == "bgx2Z4LwNGvFN6Fy0tg9ti6YuAhZOFpIT8Ajj8aIoDo"

    # Custom secret key produces a distinct signature with non-alphanumeric base64 chars stripped
    custom_sign = compute_handshake_signature(
        token, conn_type, random_val, secret_key="custom-secret"
    )
    assert custom_sign == "OSKqe0GJzOrQsI4vo36ngmiOSYYcXdHzmgAWftVxgo"

    params = SocketHandshakeParams(
        token=token,
        type=conn_type,
        random=random_val,
        sign=sign,
    )

    assert verify_handshake_signature(params) is True


def test_verify_handshake_signature_invalid() -> None:
    params = SocketHandshakeParams(
        token="test-token",
        type="file",
        random="rnd123",
        sign="wrong_signature",
    )

    assert verify_handshake_signature(params) is False


def test_verify_handshake_signature_missing_sign() -> None:
    params = SocketHandshakeParams(
        token="test-token",
        type="file",
        random="rnd123",
        sign="",
    )
    assert verify_handshake_signature(params) is False


def test_verify_handshake_signature_rejects_tampered_fields() -> None:
    sign = compute_handshake_signature("test-token", "SN100", "rnd123")
    assert (
        verify_handshake_signature(
            SocketHandshakeParams(
                token="other-token", type="SN100", random="rnd123", sign=sign
            )
        )
        is False
    )
    assert (
        verify_handshake_signature(
            SocketHandshakeParams(
                token="test-token", type="SN200", random="rnd123", sign=sign
            )
        )
        is False
    )
    assert (
        verify_handshake_signature(
            SocketHandshakeParams(
                token="test-token", type="SN100", random="rnd999", sign=sign
            )
        )
        is False
    )
