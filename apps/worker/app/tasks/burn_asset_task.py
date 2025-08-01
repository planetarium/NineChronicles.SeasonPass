import hashlib
from datetime import datetime, timezone
from typing import Any, Dict

import structlog
from app.celery_app import app
from app.config import config
from app.utils.aws import Account
from shared.enums import PlanetID
from shared.models.user import Claim
from shared.utils._graphql import GQLClient
from shared.utils.transaction import create_burn_asset_unsigned_tx, create_signed_tx
from sqlalchemy import create_engine, desc, select
from sqlalchemy.orm import scoped_session, sessionmaker

logger = structlog.get_logger(__name__)
engine = create_engine(str(config.pg_dsn), pool_size=5, max_overflow=5)


def get_decimal_places_for_ticker(ticker: str) -> int:
    """티커별 decimal places를 반환합니다."""
    ticker_decimal_map = {
        "NCG": 2,
        "CRYSTAL": 18,
        "MEAD": 18,
    }
    return ticker_decimal_map.get(ticker.upper(), 0)  # 기본값: 0


@app.task(
    name="season_pass.process_burn_asset",
    bind=True,
    max_retries=30,
    default_retry_delay=60,
    acks_late=True,
    priority=0,
    queue="claim_queue",
)
def process_burn_asset(self, message: Dict[str, Any]) -> str:
    """
    Process burn asset messages as a Celery task

    Args:
        self: Task instance
        message: The message data containing burn asset information

    Returns:
        str: Processing result message
    """
    sess = scoped_session(sessionmaker(bind=engine))
    account = Account(config.kms_key_id, config.region_name)
    gql = GQLClient(config.converted_gql_url_map, config.headless_jwt_secret)

    try:
        logger.info("Processing burn asset message", message=message)

        # Extract data from message
        ticker = message.get("ticker")
        amount = str(message.get("amount"))
        memo = message.get("memo", "")
        planet_id = message.get("planet_id", "0x000000000000")  # 기본값: ODIN

        # Get decimal places for ticker
        decimal_places = get_decimal_places_for_ticker(ticker)

        # Create Account instance to get the signer address
        owner_hex = account.address

        # Get nonce (GQL과 Claim 테이블의 nonce 중 큰 값 사용)
        planet_id_enum = PlanetID(planet_id.encode())
        nonce = max(
            gql.get_next_nonce(planet_id_enum, account.address),
            (
                sess.scalar(
                    select(Claim.nonce)
                    .where(
                        Claim.nonce.is_not(None),
                        Claim.planet_id == planet_id_enum,
                    )
                    .order_by(desc(Claim.nonce))
                    .limit(1)
                )
                or -1
            )
            + 1,
        )

        # Create unsigned transaction
        unsigned_tx = create_burn_asset_unsigned_tx(
            planet_id=planet_id_enum,
            public_key=account.pubkey.hex(),
            address=account.address,
            nonce=nonce,
            owner=owner_hex,
            ticker=ticker,
            decimal_places=decimal_places,
            amount=amount,
            memo=memo,
            timestamp=datetime.now(tz=timezone.utc),
        )

        # AWS KMS로 서명 생성
        signature = account.sign_tx(unsigned_tx)

        # Signed transaction 생성
        signed_tx = create_signed_tx(unsigned_tx, signature)

        # Transaction ID 생성
        tx_id = hashlib.sha256(signed_tx).hexdigest()

        logger.info(
            "Burn asset transaction created successfully",
            owner=owner_hex,
            ticker=ticker,
            amount=amount,
            memo=memo,
            nonce=nonce,
            tx_id=tx_id,
            tx_hex=signed_tx.hex(),
        )

        # Stage transaction
        success, msg, _ = gql.stage(planet_id_enum, signed_tx)
        if not success:
            error_message = f"Failed to stage burn asset tx with nonce {nonce}: {msg}"
            logger.error(error_message)
            raise Exception(error_message)

        logger.info("Burn asset transaction staged successfully", tx_id=tx_id)

        return "Burn asset transaction processed and staged successfully"

    except Exception as exc:
        logger.error(
            "Error processing burn asset message", message=message, exc_info=exc
        )
        self.retry(exc=exc)
    finally:
        sess.close()
