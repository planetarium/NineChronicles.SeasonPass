from enum import Enum


class TxStatus(Enum):
    """
    # Transaction Status
    ---
    Transaction status from IAP service to buyer to send purchased items.

    - **`Created`**

        The transaction is created, successfully signed and ready to stage.

    - **`Staged`**

        The transaction is successfully stated into the chain.

    - **`Success`**

        The transaction is successfully added to block.

    - **`Failure`**

        The transaction is failed.

    - **`Invalid`**

        The transaction is invalid.
        If you see this status, please contact to administrator.

    - **`Not Found`**

        The transaction is not found in chain.

    - **`Fail to Create`**

        Transaction creation is failed.
        If you see this status, please contact to administrator.

    - **`Unknown`**

        An unhandled error case. This is reserve to catch all other errors.
        If you see this status, please contact with administrator.

    """
    CREATED = "Created"
    STAGED = "Staged"
    SUCCESS = "Success"
    FAILURE = "Failure"
    INVALID = "Invalid"
    NOT_FOUND = "Not Found"
    FAIL_TO_CREATE = "Fail to Create"
    UNKNOWN = "Unknown"
