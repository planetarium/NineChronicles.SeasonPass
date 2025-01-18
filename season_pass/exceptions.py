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
