from unittest.mock import Mock, patch

from shared.utils.actions import (
    Address,
    BurnAsset,
    ClaimItems,
    FungibleAssetValue,
    GrantItems,
)


def test_claim_items_plain_value():
    claim_items = ClaimItems(
        claim_data=[
            {
                "avatarAddress": Address("0x8bA11bEf1DB41F3118f7478cCfcbE7f1Af4650fa"),
                "fungibleAssetValues": [
                    FungibleAssetValue.from_raw_data(
                        ticker="Item_NT_500000",
                        decimal_places=0,
                        minters=None,
                        amount=1,
                    )
                ],
            }
        ],
        memo="claim",
        _id="ae6d745a6dc911f0a9005210846b4679",
    )
    plain = claim_items.plain_value
    assert plain["type_id"] == "claim_items"
    assert plain["values"]["id"] == bytes.fromhex("ae6d745a6dc911f0a9005210846b4679")
    assert plain["values"]["m"] == "claim"
    assert isinstance(plain["values"]["cd"][0][0], bytes)
    assert isinstance(plain["values"]["cd"][0][1][0], list)


def test_claim_items_serialized_plain_value():
    claim_items = ClaimItems(
        claim_data=[
            {
                "avatarAddress": Address("0x8bA11bEf1DB41F3118f7478cCfcbE7f1Af4650fa"),
                "fungibleAssetValues": [
                    FungibleAssetValue.from_raw_data(
                        ticker="Item_NT_500000",
                        decimal_places=0,
                        minters=None,
                        amount=1,
                    )
                ],
            }
        ],
        memo="claim",
        _id="ae6d745a6dc911f0a9005210846b4679",
    )
    # 직렬화가 정상적으로 동작하는지만 확인
    serialized = claim_items.serialized_plain_value
    assert isinstance(serialized, bytes)
    assert b"claim_items" in serialized or b"claim_items" in serialized.decode(
        errors="ignore"
    )


def test_grant_items_plain_value():
    grant_items = GrantItems(
        claim_data=[
            {
                "avatarAddress": Address("0x8bA11bEf1DB41F3118f7478cCfcbE7f1Af4650fa"),
                "fungibleAssetValues": [
                    FungibleAssetValue.from_raw_data(
                        ticker="Item_NT_500000",
                        decimal_places=0,
                        minters=None,
                        amount=1,
                    )
                ],
            }
        ],
        memo="memo",
    )
    plain = grant_items.plain_value
    assert plain["type_id"] == "grant_items"
    assert plain["values"]["id"] == bytes.fromhex(grant_items._id)
    assert plain["values"]["m"] == "memo"
    assert isinstance(plain["values"]["cd"][0][0], bytes)
    assert isinstance(plain["values"]["cd"][0][1][0], list)


def test_grant_items_omit_empty_memo():
    grant_items = GrantItems(
        claim_data=[
            {
                "avatarAddress": Address("0x8bA11bEf1DB41F3118f7478cCfcbE7f1Af4650fa"),
                "fungibleAssetValues": [
                    FungibleAssetValue.from_raw_data(
                        ticker="Item_NT_500000",
                        decimal_places=0,
                        minters=None,
                        amount=1,
                    )
                ],
            }
        ],
        memo="",
    )
    plain = grant_items.plain_value
    assert plain["type_id"] == "grant_items"
    assert plain["values"]["id"] == bytes.fromhex(grant_items._id)
    assert "m" not in plain["values"]


def test_burn_asset_plain_value():
    owner = Address("0x8bA11bEf1DB41F3118f7478cCfcbE7f1Af4650fa")
    amount = FungibleAssetValue.from_raw_data(
        ticker="Mead", decimal_places=0, minters=None, amount=100
    )
    burn = BurnAsset(
        owner=owner,
        amount=amount,
        memo="burn-test",
        _id="1234567890abcdef1234567890abcdef",
    )
    plain = burn.plain_value
    assert plain["type_id"] == "burn_asset"
    assert plain["values"][0] == owner.raw
    assert plain["values"][1] == amount.plain_value
    assert plain["values"][2] == "burn-test"


def test_burn_asset_serialized_plain_value():
    owner = Address("0x8bA11bEf1DB41F3118f7478cCfcbE7f1Af4650fa")
    amount = FungibleAssetValue.from_raw_data(
        ticker="Mead", decimal_places=0, minters=None, amount=100
    )
    burn = BurnAsset(
        owner=owner,
        amount=amount,
        memo="burn-test",
        _id="1234567890abcdef1234567890abcdef",
    )
    serialized = burn.serialized_plain_value
    assert isinstance(serialized, bytes)
    assert b"burn_asset" in serialized or b"burn_asset" in serialized.decode(
        errors="ignore"
    )


# AWS KMS 서명 관련 테스트
@patch("boto3.client")
def test_account_sign_tx(mock_boto3_client):
    """AWS KMS를 사용한 트랜잭션 서명 테스트"""
    from apps.worker.app.utils.aws import Account

    # Mock KMS 클라이언트 설정
    mock_kms_client = Mock()
    mock_boto3_client.return_value = mock_kms_client

    # Mock public key 응답
    mock_kms_client.get_public_key.return_value = {
        "PublicKey": b"mock_public_key_der_encoded"
    }

    # Mock 서명 응답
    mock_kms_client.sign.return_value = {"Signature": b"mock_signature_bytes"}

    # Account 인스턴스 생성 (실제 KMS 호출 없이)
    with patch("apps.worker.app.utils.aws.der_decode") as mock_der_decode:
        # Mock DER 디코딩 결과
        mock_der_decode.return_value = (Mock(), None)

        # Mock address 계산
        with patch("apps.worker.app.utils.aws.to_checksum_address") as mock_checksum:
            mock_checksum.return_value = "0x1234567890123456789012345678901234567890"

            account = Account("mock-key-id", "us-east-1")

            # 테스트용 unsigned transaction
            unsigned_tx = b"test_unsigned_transaction_bytes"

            # Mock 서명 파라미터 계산
            with patch("apps.worker.app.utils.aws.hashlib.sha256") as mock_sha256:
                mock_sha256.return_value.digest.return_value = b"mock_hash"

                # Mock r, s 값
                with patch("apps.worker.app.utils.aws.SequenceOf") as mock_seq:
                    mock_seq.return_value.extend.return_value = None

                    # Mock DER 인코딩
                    with patch(
                        "apps.worker.app.utils.aws.der_encode"
                    ) as mock_der_encode:
                        mock_der_encode.return_value = b"mock_der_signature"

                        # sign_tx 메서드 호출
                        result = account.sign_tx(unsigned_tx)

                        # 검증
                        assert result == b"mock_der_signature"
                        mock_kms_client.sign.assert_called_once()


def test_account_initialization():
    """Account 클래스 초기화 테스트"""
    from apps.worker.app.utils.aws import Account

    with patch("boto3.client") as mock_boto3_client:
        mock_kms_client = Mock()
        mock_boto3_client.return_value = mock_kms_client

        # Mock public key 응답
        mock_kms_client.get_public_key.return_value = {
            "PublicKey": b"mock_public_key_der_encoded"
        }

        with patch("apps.worker.app.utils.aws.der_decode") as mock_der_decode:
            mock_der_decode.return_value = (Mock(), None)

            with patch(
                "apps.worker.app.utils.aws.to_checksum_address"
            ) as mock_checksum:
                mock_checksum.return_value = (
                    "0x1234567890123456789012345678901234567890"
                )

                # Account 인스턴스 생성
                account = Account("mock-key-id", "us-east-1")

                # 검증
                assert account._kms_key == "mock-key-id"
                assert account.address == "0x1234567890123456789012345678901234567890"
                assert hasattr(account, "pubkey")
                assert hasattr(account, "pubkey_der")


def test_account_get_item_garage_addr():
    """get_item_garage_addr 메서드 테스트"""
    from apps.worker.app.utils.aws import Account

    with patch("boto3.client") as mock_boto3_client:
        mock_kms_client = Mock()
        mock_boto3_client.return_value = mock_kms_client

        # Mock public key 응답
        mock_kms_client.get_public_key.return_value = {
            "PublicKey": b"mock_public_key_der_encoded"
        }

        with patch("apps.worker.app.utils.aws.der_decode") as mock_der_decode:
            mock_der_decode.return_value = (Mock(), None)

            with patch(
                "apps.worker.app.utils.aws.to_checksum_address"
            ) as mock_checksum:
                mock_checksum.return_value = (
                    "0x1234567890123456789012345678901234567890"
                )

                account = Account("mock-key-id", "us-east-1")

                # get_item_garage_addr 메서드 테스트
                with patch("apps.worker.app.utils.aws.derive_address") as mock_derive:
                    mock_derive.return_value = "0xgarage_address"

                    result = account.get_item_garage_addr("item_id_123")

                    # 검증
                    assert result == "0xgarage_address"
                    assert mock_derive.call_count == 2  # derive_address가 두 번 호출됨
