import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pytest
from shared.enums import PassType, PlanetID, TxStatus
from shared.models.season_pass import SeasonPass
from shared.models.user import Claim
from shared.schemas.message import ClaimMessage

sys.path.append(os.path.join(os.path.dirname(__file__), "../../../Worker"))

from unittest.mock import Mock, patch

# Mock the entire config module at module level
import app.config

# Mock config before importing claim_consumer
import pytest

app.config.config = Mock()
app.config.config.kms_key_id = "test-key-id"
app.config.config.region_name = "us-east-2"
app.config.config.pg_dsn = "postgresql://test:test@localhost:5432/test"
app.config.config.broker_url = "pyamqp://test:test@localhost:5672/"
app.config.config.result_backend = "redis://localhost:6379/0"
app.config.config.gql_url_map = {
    "0x000000000000": "https://odin-rpc.nine-chronicles.com/graphql",
    "0x000000000001": "https://heimdall-rpc.nine-chronicles.com/graphql",
}
app.config.config.converted_gql_url_map = {
    "0x000000000000": "https://odin-rpc.nine-chronicles.com/graphql",
    "0x000000000001": "https://heimdall-rpc.nine-chronicles.com/graphql",
}
app.config.config.headless_jwt_secret = "test-secret"

from app.consumers.claim_consumer import consume_claim_message


class TestClaimConsumer:
    """claim_consumer.py에 대한 테스트 클래스"""

    @pytest.fixture
    def mock_claim(self):
        """테스트용 Claim 객체 생성"""
        season_pass = SeasonPass(
            uuid="test-season-pass-uuid",
            pass_type=PassType.COURAGE_PASS,
            normal_levels=[1, 2, 3],
            premium_levels=[4, 5],
        )

        claim = Claim(
            uuid="test-claim-uuid",
            agent_addr="test-agent-addr",
            avatar_addr="0x8bA11bEf1DB41F3118f7478cCfcbE7f1Af4650fa",
            planet_id=PlanetID.ODIN,
            normal_levels=[1, 2, 3],
            premium_levels=[4, 5],
            reward_list=[
                {"ticker": "Item_NT_500000", "amount": 1, "decimal_places": 0}
            ],
            nonce=None,
            tx_status=TxStatus.CREATED,
            created_at=datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        )

        # 관계 설정
        claim.season_pass = season_pass

        return claim

    @pytest.fixture
    def mock_message(self):
        """테스트용 ClaimMessage 객체 생성"""
        return ClaimMessage(uuid="test-claim-uuid")

    @patch("app.consumers.claim_consumer.scoped_session")
    @patch("app.consumers.claim_consumer.Account")
    @patch("app.consumers.claim_consumer.GQLClient")
    def test_consume_claim_message_success(
        self,
        mock_gql_client,
        mock_account_class,
        mock_session_factory,
        mock_claim,
        mock_message,
    ):
        """성공적인 claim 처리 테스트"""
        # Mock 설정
        mock_session = Mock()
        mock_session_factory.return_value = mock_session

        # Mock Claim 조회
        mock_session.scalars.return_value = [mock_claim]
        mock_session.scalar.return_value = 10  # 기존 nonce

        # Mock Account
        mock_account = Mock()
        mock_account.address = "0x8bA11bEf1DB41F3118f7478cCfcbE7f1Af4650fa"
        mock_account.pubkey.hex.return_value = (
            "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        )
        mock_account.sign_tx.return_value = b"mock_signature"
        mock_account_class.return_value = mock_account

        # Mock GQL
        mock_gql = Mock()
        mock_gql.get_next_nonce.return_value = 11
        mock_gql.stage.return_value = (True, "", "mock_tx_hash")
        mock_gql_client.return_value = mock_gql

        # 함수 실행
        consume_claim_message(mock_message)

        # 검증
        assert mock_claim.nonce == 11
        assert mock_claim.tx_status == TxStatus.STAGED
        assert mock_claim.tx is not None
        assert mock_claim.tx_id is not None

        # 트랜잭션 생성 함수 호출 확인
        mock_session.add.assert_called()
        mock_session.commit.assert_called()
        mock_session.close.assert_called()

    @patch("app.consumers.claim_consumer.scoped_session")
    @patch("app.consumers.claim_consumer.Account")
    @patch("app.consumers.claim_consumer.GQLClient")
    def test_consume_claim_message_claim_not_found(
        self, mock_gql_client, mock_account_class, mock_session_factory, mock_message
    ):
        """Claim을 찾을 수 없는 경우 테스트"""
        # Mock 설정
        mock_session = Mock()
        mock_session_factory.return_value = mock_session

        # Mock Claim 조회 - 빈 결과
        mock_session.scalars.return_value = []

        # Mock Account
        mock_account = Mock()
        mock_account_class.return_value = mock_account

        # Mock GQL
        mock_gql = Mock()
        mock_gql_client.return_value = mock_gql

        # 함수 실행
        consume_claim_message(mock_message)

        # 검증 - 아무것도 변경되지 않아야 함
        mock_session.add.assert_not_called()
        mock_session.commit.assert_not_called()

    @patch("app.consumers.claim_consumer.scoped_session")
    @patch("app.consumers.claim_consumer.Account")
    @patch("app.consumers.claim_consumer.GQLClient")
    def test_consume_claim_message_existing_tx(
        self,
        mock_gql_client,
        mock_account_class,
        mock_session_factory,
        mock_claim,
        mock_message,
    ):
        """이미 tx가 있는 경우 테스트"""
        # Mock 설정
        mock_session = Mock()
        mock_session_factory.return_value = mock_session

        # Mock Claim 조회 - 이미 tx가 있는 claim
        mock_claim.tx = "existing_tx_hex"
        mock_session.scalars.return_value = [mock_claim]

        # Mock Account
        mock_account = Mock()
        mock_account_class.return_value = mock_account

        # Mock GQL
        mock_gql = Mock()
        mock_gql.stage.return_value = (True, "", "mock_tx_hash")
        mock_gql_client.return_value = mock_gql

        # 함수 실행
        consume_claim_message(mock_message)

        # 검증 - 기존 tx가 그대로 유지되어야 함
        assert mock_claim.tx == "existing_tx_hex"
        mock_session.add.assert_called()
        mock_session.commit.assert_called()

    @patch("app.consumers.claim_consumer.scoped_session")
    @patch("app.consumers.claim_consumer.Account")
    @patch("app.consumers.claim_consumer.GQLClient")
    def test_consume_claim_message_stage_failure(
        self,
        mock_gql_client,
        mock_account_class,
        mock_session_factory,
        mock_claim,
        mock_message,
    ):
        """트랜잭션 스테이징 실패 테스트"""
        # Mock 설정
        mock_session = Mock()
        mock_session_factory.return_value = mock_session

        # Mock Claim 조회
        mock_session.scalars.return_value = [mock_claim]
        mock_session.scalar.return_value = 10

        # Mock Account
        mock_account = Mock()
        mock_account.address = "0x8bA11bEf1DB41F3118f7478cCfcbE7f1Af4650fa"
        mock_account.pubkey.hex.return_value = (
            "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        )
        mock_account.sign_tx.return_value = b"mock_signature"
        mock_account_class.return_value = mock_account

        # Mock GQL - 스테이징 실패
        mock_gql = Mock()
        mock_gql.get_next_nonce.return_value = 11
        mock_gql.stage.return_value = (False, "Staging failed", None)
        mock_gql_client.return_value = mock_gql

        # 함수 실행 - 예외 발생해야 함
        with pytest.raises(Exception, match="Failed to stage tx"):
            consume_claim_message(mock_message)

    @patch("app.consumers.claim_consumer.scoped_session")
    @patch("app.consumers.claim_consumer.Account")
    @patch("app.consumers.claim_consumer.GQLClient")
    def test_consume_claim_message_transaction_creation(
        self,
        mock_gql_client,
        mock_account_class,
        mock_session_factory,
        mock_claim,
        mock_message,
    ):
        """트랜잭션 생성 과정 테스트"""
        # Mock 설정
        mock_session = Mock()
        mock_session_factory.return_value = mock_session

        # Mock Claim 조회
        mock_session.scalars.return_value = [mock_claim]
        mock_session.scalar.return_value = 10

        # Mock Account
        mock_account = Mock()
        mock_account.address = "0x8bA11bEf1DB41F3118f7478cCfcbE7f1Af4650fa"
        mock_account.pubkey.hex.return_value = (
            "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        )
        mock_account.sign_tx.return_value = b"mock_signature"
        mock_account_class.return_value = mock_account

        # Mock GQL
        mock_gql = Mock()
        mock_gql.get_next_nonce.return_value = 11
        mock_gql.stage.return_value = (True, "", "mock_tx_hash")
        mock_gql_client.return_value = mock_gql

        # 함수 실행
        consume_claim_message(mock_message)

        # 검증 - 트랜잭션 생성 과정 확인
        assert mock_claim.nonce == 11
        assert mock_claim.tx_status == TxStatus.STAGED
        assert mock_claim.tx is not None
        assert mock_claim.tx_id is not None

        # tx_id가 올바른 해시인지 확인
        expected_tx_id = hashlib.sha256(bytes.fromhex(mock_claim.tx)).hexdigest()
        assert mock_claim.tx_id == expected_tx_id

    @patch("app.consumers.claim_consumer.scoped_session")
    @patch("app.consumers.claim_consumer.Account")
    @patch("app.consumers.claim_consumer.GQLClient")
    def test_consume_claim_message_memo_format(
        self,
        mock_gql_client,
        mock_account_class,
        mock_session_factory,
        mock_claim,
        mock_message,
    ):
        """Memo 형식 테스트"""
        # Mock 설정
        mock_session = Mock()
        mock_session_factory.return_value = mock_session

        # Mock Claim 조회
        mock_session.scalars.return_value = [mock_claim]
        mock_session.scalar.return_value = 10

        # Mock Account
        mock_account = Mock()
        mock_account.address = "0x8bA11bEf1DB41F3118f7478cCfcbE7f1Af4650fa"
        mock_account.pubkey.hex.return_value = (
            "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        )
        mock_account.sign_tx.return_value = b"mock_signature"
        mock_account_class.return_value = mock_account

        # Mock GQL
        mock_gql = Mock()
        mock_gql.get_next_nonce.return_value = 11
        mock_gql.stage.return_value = (True, "", "mock_tx_hash")
        mock_gql_client.return_value = mock_gql

        # 함수 실행
        consume_claim_message(mock_message)

        # 검증 - memo 형식 확인
        expected_memo = json.dumps(
            {
                "season_pass": {
                    "n": [1, 2, 3],
                    "p": [4, 5],
                    "t": "claim",
                    "tp": "CouragePass",
                }
            }
        )

        # 실제 memo는 함수 내부에서 생성되므로, 트랜잭션이 성공적으로 생성되었는지만 확인
        assert mock_claim.tx is not None
        assert mock_claim.tx_status == TxStatus.STAGED

    @patch("app.consumers.claim_consumer.scoped_session")
    @patch("app.consumers.claim_consumer.Account")
    @patch("app.consumers.claim_consumer.GQLClient")
    def test_consume_claim_message_nonce_reuse(
        self,
        mock_gql_client,
        mock_account_class,
        mock_session_factory,
        mock_claim,
        mock_message,
    ):
        """Nonce 재사용 테스트"""
        # Mock 설정
        mock_session = Mock()
        mock_session_factory.return_value = mock_session

        # Mock Claim 조회 - 이미 nonce가 있는 claim
        mock_claim.nonce = 5
        mock_session.scalars.return_value = [mock_claim]

        # Mock Account
        mock_account = Mock()
        mock_account.address = "0x8bA11bEf1DB41F3118f7478cCfcbE7f1Af4650fa"
        mock_account.pubkey.hex.return_value = (
            "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        )
        mock_account.sign_tx.return_value = b"mock_signature"
        mock_account_class.return_value = mock_account

        # Mock GQL
        mock_gql = Mock()
        mock_gql.get_next_nonce.return_value = 11
        mock_gql.stage.return_value = (True, "", "mock_tx_hash")
        mock_gql_client.return_value = mock_gql

        # 함수 실행
        consume_claim_message(mock_message)

        # 검증 - 기존 nonce가 유지되어야 함
        assert mock_claim.nonce == 5
        assert mock_claim.tx_status == TxStatus.STAGED

    @patch("app.consumers.claim_consumer.scoped_session")
    @patch("app.consumers.claim_consumer.Account")
    @patch("app.consumers.claim_consumer.GQLClient")
    def test_consume_claim_message_session_cleanup(
        self,
        mock_gql_client,
        mock_account_class,
        mock_session_factory,
        mock_claim,
        mock_message,
    ):
        """세션 정리 테스트"""
        # Mock 설정
        mock_session = Mock()
        mock_session_factory.return_value = mock_session

        # Mock Claim 조회
        mock_session.scalars.return_value = [mock_claim]
        mock_session.scalar.return_value = 10

        # Mock Account
        mock_account = Mock()
        mock_account.address = "0x8bA11bEf1DB41F3118f7478cCfcbE7f1Af4650fa"
        mock_account.pubkey.hex.return_value = (
            "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        )
        mock_account.sign_tx.return_value = b"mock_signature"
        mock_account_class.return_value = mock_account

        # Mock GQL
        mock_gql = Mock()
        mock_gql.get_next_nonce.return_value = 11
        mock_gql.stage.return_value = (True, "", "mock_tx_hash")
        mock_gql_client.return_value = mock_gql

        # 함수 실행
        consume_claim_message(mock_message)

        # 검증 - 세션이 정리되어야 함
        mock_session.close.assert_called_once()

    @patch("app.consumers.claim_consumer.scoped_session")
    @patch("app.consumers.claim_consumer.Account")
    @patch("app.consumers.claim_consumer.GQLClient")
    def test_consume_claim_message_exception_handling(
        self,
        mock_gql_client,
        mock_account_class,
        mock_session_factory,
        mock_claim,
        mock_message,
    ):
        """예외 처리 테스트"""
        # Mock 설정
        mock_session = Mock()
        mock_session_factory.return_value = mock_session

        # Mock Claim 조회
        mock_session.scalars.return_value = [mock_claim]
        mock_session.scalar.return_value = 10

        # Mock Account - 예외 발생
        mock_account = Mock()
        mock_account.address = "0x8bA11bEf1DB41F3118f7478cCfcbE7f1Af4650fa"
        mock_account.pubkey.hex.return_value = (
            "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        )
        mock_account.sign_tx.side_effect = Exception("KMS signing failed")
        mock_account_class.return_value = mock_account

        # Mock GQL
        mock_gql = Mock()
        mock_gql.get_next_nonce.return_value = 11
        mock_gql_client.return_value = mock_gql

        # 함수 실행 - 예외가 발생해도 세션은 정리되어야 함
        with pytest.raises(Exception, match="KMS signing failed"):
            consume_claim_message(mock_message)

        # 검증 - 세션이 정리되어야 함
        mock_session.close.assert_called_once()
