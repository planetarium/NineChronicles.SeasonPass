from unittest.mock import MagicMock, patch

import pytest
from app.tasks.burn_asset_task import get_decimal_places_for_ticker, process_burn_asset


class TestBurnAssetTask:
    """Burn Asset Task 테스트 클래스"""

    def test_get_decimal_places_for_ticker(self):
        """티커별 decimal places 테스트"""
        assert get_decimal_places_for_ticker("NCG") == 2
        assert get_decimal_places_for_ticker("CRYSTAL") == 18
        assert get_decimal_places_for_ticker("MEAD") == 18
        assert get_decimal_places_for_ticker("GOLD") == 0  # 기본값
        assert get_decimal_places_for_ticker("UNKNOWN") == 0  # 기본값
        assert get_decimal_places_for_ticker("ncg") == 2  # 대소문자 구분 없음

    def test_process_burn_asset_success(self):
        """정상적인 burn asset 처리 테스트"""
        message = {
            "ticker": "NCG",
            "amount": "10.5",
            "memo": "Test burn asset",
            "planet_id": "0x000000000000",
        }

        with patch(
            "app.tasks.burn_asset_task.structlog.get_logger"
        ) as mock_logger, patch(
            "app.tasks.burn_asset_task.Account"
        ) as mock_account_class, patch(
            "app.tasks.burn_asset_task.GQLClient"
        ) as mock_gql_class, patch(
            "app.tasks.burn_asset_task.scoped_session"
        ) as mock_session:

            mock_logger_instance = MagicMock()
            mock_logger.return_value = mock_logger_instance

            mock_account = MagicMock()
            mock_account.address = "0x1234567890abcdef1234567890abcdef12345678"
            mock_account.pubkey.hex.return_value = (
                "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
            )
            mock_account.sign_tx.return_value = b"mock_signature"
            mock_account_class.return_value = mock_account

            mock_gql = MagicMock()
            mock_gql.get_next_nonce.return_value = 123
            mock_gql.stage.return_value = (True, "Success", None)
            mock_gql_class.return_value = mock_gql

            # Mock DB session
            mock_sess = MagicMock()
            mock_sess.scalar.return_value = 100  # DB에서 가져온 nonce
            mock_session.return_value = mock_sess

            result = process_burn_asset(message)

            # 로그 호출 확인
            assert mock_logger_instance.info.call_count >= 2
            assert mock_logger_instance.error.call_count == 0

            # 결과 확인
            assert result == "Burn asset transaction processed and staged successfully"

    def test_process_burn_asset_with_default_values(self):
        """기본값을 사용한 burn asset 처리 테스트"""
        message = {
            "ticker": "CRYSTAL",
            "amount": "5.0"
            # memo, planet_id는 기본값 사용
        }

        with patch(
            "app.tasks.burn_asset_task.structlog.get_logger"
        ) as mock_logger, patch(
            "app.tasks.burn_asset_task.Account"
        ) as mock_account_class, patch(
            "app.tasks.burn_asset_task.GQLClient"
        ) as mock_gql_class, patch(
            "app.tasks.burn_asset_task.scoped_session"
        ) as mock_session:

            mock_logger_instance = MagicMock()
            mock_logger.return_value = mock_logger_instance

            mock_account = MagicMock()
            mock_account.address = "0x1234567890abcdef1234567890abcdef12345678"
            mock_account.pubkey.hex.return_value = (
                "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
            )
            mock_account.sign_tx.return_value = b"mock_signature"
            mock_account_class.return_value = mock_account

            mock_gql = MagicMock()
            mock_gql.get_next_nonce.return_value = 456
            mock_gql.stage.return_value = (True, "Success", None)
            mock_gql_class.return_value = mock_gql

            # Mock DB session
            mock_sess = MagicMock()
            mock_sess.scalar.return_value = None  # DB에 nonce가 없는 경우
            mock_session.return_value = mock_sess

            result = process_burn_asset(message)

            # 로그 호출 확인
            assert mock_logger_instance.info.call_count >= 2
            assert result == "Burn asset transaction processed and staged successfully"

    def test_process_burn_asset_missing_required_fields(self):
        """필수 필드 누락 테스트"""
        message = {
            "amount": "10.5"
            # ticker 필드 누락
        }

        with patch("app.tasks.burn_asset_task.structlog.get_logger") as mock_logger:
            mock_logger_instance = MagicMock()
            mock_logger.return_value = mock_logger_instance

            with pytest.raises(Exception):
                process_burn_asset(message)

            # 에러 로그 호출 확인
            assert mock_logger_instance.error.call_count >= 1

    def test_process_burn_asset_invalid_amount(self):
        """잘못된 amount 값 테스트"""
        message = {
            "ticker": "NCG",
            "amount": "invalid_amount",
            "memo": "Test burn asset",
        }

        with patch("app.tasks.burn_asset_task.structlog.get_logger") as mock_logger:
            mock_logger_instance = MagicMock()
            mock_logger.return_value = mock_logger_instance

            with pytest.raises(Exception):
                process_burn_asset(message)

            # 에러 로그 호출 확인
            assert mock_logger_instance.error.call_count >= 1

    def test_process_burn_asset_different_currencies(self):
        """다양한 통화에 대한 테스트"""
        test_cases = [
            {
                "ticker": "NCG",
                "amount": "10.5",
                "memo": "NCG burn test",
                "planet_id": "0x000000000000",
            },
            {
                "ticker": "CRYSTAL",
                "amount": "100",
                "memo": "CRYSTAL burn test",
                "planet_id": "0x000000000001",
            },
            {
                "ticker": "GOLD",
                "amount": "0.00000001",
                "memo": "GOLD burn test",
                "planet_id": "0x000000000000",
            },
        ]

        with patch(
            "app.tasks.burn_asset_task.structlog.get_logger"
        ) as mock_logger, patch(
            "app.tasks.burn_asset_task.Account"
        ) as mock_account_class, patch(
            "app.tasks.burn_asset_task.GQLClient"
        ) as mock_gql_class, patch(
            "app.tasks.burn_asset_task.scoped_session"
        ) as mock_session:

            mock_logger_instance = MagicMock()
            mock_logger.return_value = mock_logger_instance

            mock_account = MagicMock()
            mock_account.address = "0x1234567890abcdef1234567890abcdef12345678"
            mock_account.pubkey.hex.return_value = (
                "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
            )
            mock_account.sign_tx.return_value = b"mock_signature"
            mock_account_class.return_value = mock_account

            mock_gql = MagicMock()
            mock_gql.get_next_nonce.return_value = 789
            mock_gql.stage.return_value = (True, "Success", None)
            mock_gql_class.return_value = mock_gql

            # Mock DB session
            mock_sess = MagicMock()
            mock_sess.scalar.return_value = 500  # DB에서 가져온 nonce
            mock_session.return_value = mock_sess

            for test_case in test_cases:
                result = process_burn_asset(test_case)
                assert (
                    result == "Burn asset transaction processed and staged successfully"
                )

    def test_process_burn_asset_staging_failure(self):
        """스테이징 실패 테스트"""
        message = {"ticker": "NCG", "amount": "10.5", "planet_id": "0x000000000000"}

        with patch(
            "app.tasks.burn_asset_task.structlog.get_logger"
        ) as mock_logger, patch(
            "app.tasks.burn_asset_task.Account"
        ) as mock_account_class, patch(
            "app.tasks.burn_asset_task.GQLClient"
        ) as mock_gql_class, patch(
            "app.tasks.burn_asset_task.scoped_session"
        ) as mock_session:

            mock_logger_instance = MagicMock()
            mock_logger.return_value = mock_logger_instance

            mock_account = MagicMock()
            mock_account.address = "0x1234567890abcdef1234567890abcdef12345678"
            mock_account.pubkey.hex.return_value = (
                "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
            )
            mock_account.sign_tx.return_value = b"mock_signature"
            mock_account_class.return_value = mock_account

            mock_gql = MagicMock()
            mock_gql.get_next_nonce.return_value = 123
            mock_gql.stage.return_value = (False, "Staging failed", None)
            mock_gql_class.return_value = mock_gql

            # Mock DB session
            mock_sess = MagicMock()
            mock_sess.scalar.return_value = 100
            mock_session.return_value = mock_sess

            with pytest.raises(Exception) as exc_info:
                process_burn_asset(message)

            assert "Failed to stage burn asset tx" in str(exc_info.value)
            assert mock_logger_instance.error.call_count >= 1

    def test_process_burn_asset_account_error(self):
        """Account 생성 중 오류 발생 테스트"""
        message = {"ticker": "NCG", "amount": "10.5"}

        with patch(
            "app.tasks.burn_asset_task.structlog.get_logger"
        ) as mock_logger, patch(
            "app.tasks.burn_asset_task.Account"
        ) as mock_account_class:

            mock_logger_instance = MagicMock()
            mock_logger.return_value = mock_logger_instance

            # Account 생성 시 예외 발생하도록 모킹
            mock_account_class.side_effect = Exception("Account creation failed")

            with pytest.raises(Exception):
                process_burn_asset(message)

            # 에러 로그 호출 확인
            assert mock_logger_instance.error.call_count >= 1

    def test_process_burn_asset_nonce_calculation(self):
        """Nonce 계산 로직 테스트"""
        message = {"ticker": "NCG", "amount": "10.5", "planet_id": "0x000000000000"}

        with patch(
            "app.tasks.burn_asset_task.structlog.get_logger"
        ) as mock_logger, patch(
            "app.tasks.burn_asset_task.Account"
        ) as mock_account_class, patch(
            "app.tasks.burn_asset_task.GQLClient"
        ) as mock_gql_class, patch(
            "app.tasks.burn_asset_task.scoped_session"
        ) as mock_session:

            mock_logger_instance = MagicMock()
            mock_logger.return_value = mock_logger_instance

            mock_account = MagicMock()
            mock_account.address = "0x1234567890abcdef1234567890abcdef12345678"
            mock_account.pubkey.hex.return_value = (
                "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
            )
            mock_account.sign_tx.return_value = b"mock_signature"
            mock_account_class.return_value = mock_account

            # GQL nonce가 DB nonce보다 큰 경우
            mock_gql = MagicMock()
            mock_gql.get_next_nonce.return_value = 200  # GQL nonce
            mock_gql.stage.return_value = (True, "Success", None)
            mock_gql_class.return_value = mock_gql

            mock_sess = MagicMock()
            mock_sess.scalar.return_value = 150  # DB nonce
            mock_session.return_value = mock_sess

            result = process_burn_asset(message)
            assert result == "Burn asset transaction processed and staged successfully"

            # DB nonce가 GQL nonce보다 큰 경우
            mock_gql.get_next_nonce.return_value = 100  # GQL nonce
            mock_sess.scalar.return_value = 200  # DB nonce

            result = process_burn_asset(message)
            assert result == "Burn asset transaction processed and staged successfully"
