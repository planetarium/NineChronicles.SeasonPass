from typing import Any, Dict, Optional

from fastapi import HTTPException


class SeasonNotFoundError(Exception):
    pass


class InvalidSeasonError(Exception):
    pass


class ServerOverloadError(Exception):
    pass


class UserNotFoundError(Exception):
    pass


class InvalidUpgradeRequestError(Exception):
    pass


class NotPremiumError(Exception):
    pass


class SeasonPassError(HTTPException):
    """시즌패스 관련 기본 예외 클래스"""

    def __init__(
        self,
        status_code: int,
        error_code: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            status_code=status_code,
            detail={
                "error_code": error_code,
                "message": message,
                "details": details or {},
            },
        )


class SeasonPassNotFoundError(SeasonPassError):
    """시즌패스를 찾을 수 없을 때"""

    def __init__(self, season_pass_id: int):
        super().__init__(
            status_code=404,
            error_code="SEASON_PASS_NOT_FOUND",
            message=f"시즌패스를 찾을 수 없습니다. ID: {season_pass_id}",
            details={"season_pass_id": season_pass_id},
        )


class SeasonPassAlreadyExistsError(SeasonPassError):
    """시즌패스가 이미 존재할 때"""

    def __init__(self, pass_type: str, season_index: int):
        super().__init__(
            status_code=400,
            error_code="SEASON_PASS_ALREADY_EXISTS",
            message=f"시즌패스가 이미 존재합니다. 타입: {pass_type}, 시즌: {season_index}",
            details={"pass_type": pass_type, "season_index": season_index},
        )


class InvalidSeasonPassDataError(SeasonPassError):
    """시즌패스 데이터가 유효하지 않을 때"""

    def __init__(self, field: str, value: Any, reason: str):
        super().__init__(
            status_code=400,
            error_code="INVALID_SEASON_PASS_DATA",
            message=f"유효하지 않은 시즌패스 데이터입니다. 필드: {field}",
            details={"field": field, "value": value, "reason": reason},
        )


class ExpDataError(SeasonPassError):
    """Exp 데이터 관련 오류"""

    def __init__(self, action_type: str, reason: str):
        super().__init__(
            status_code=400,
            error_code="INVALID_EXP_DATA",
            message=f"유효하지 않은 Exp 데이터입니다. 액션: {action_type}",
            details={"action_type": action_type, "reason": reason},
        )


class SeasonPassValidationError(SeasonPassError):
    """시즌패스 검증 오류"""

    def __init__(self, validation_errors: list):
        super().__init__(
            status_code=422,
            error_code="SEASON_PASS_VALIDATION_ERROR",
            message="시즌패스 데이터 검증에 실패했습니다.",
            details={"validation_errors": validation_errors},
        )


class DatabaseError(SeasonPassError):
    """데이터베이스 관련 오류"""

    def __init__(self, operation: str, details: str):
        super().__init__(
            status_code=500,
            error_code="DATABASE_ERROR",
            message=f"데이터베이스 작업 중 오류가 발생했습니다. 작업: {operation}",
            details={"operation": operation, "details": details},
        )
