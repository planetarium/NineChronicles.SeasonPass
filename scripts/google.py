import os
import pickle
from typing import List

from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


class Spreadsheet:
    def __init__(self, sheet_id, credential_file=None):
        self.sheet_id = sheet_id
        self._token_file = "credential.pickle"

        if not os.path.exists(self._token_file):
            self.creds = self._get_credentials(credential_file)
        else:
            with open(self._token_file, "rb") as f:
                self.creds = pickle.load(f)
            if not self.creds.valid:
                self.creds = self._get_credentials(credential_file)

        self.service = build("sheets", "v4", credentials=self.creds)

    def _get_credentials(self, credential_file: str = "credential.json"):
        creds = None
        SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

        if os.path.exists(self._token_file):
            with open(self._token_file, "rb") as token:
                creds = pickle.load(token)

        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(credential_file, SCOPES)
            creds = flow.run_local_server(port=45678)

        with open(self._token_file, "wb") as token:
            pickle.dump(creds, token)

        return creds

    def get_values(self, range_):
        result = self.service.spreadsheets().values().get(spreadsheetId=self.sheet_id, range=range_).execute()
        return result

    def get_batch_values(self, range_: List[str]):
        result = self.service.spreadsheets().values().batchGet(spreadsheetId=self.sheet_id, ranges=range_).execute()
        return result

    def set_values(self, range_, value_):
        body = {
            "value_input_option": "USER_ENTERED",
            "data": [
                {
                    "range": range_,
                    "majorDimension": "ROWS",
                    "values": value_
                }
            ]
        }
        result = self.service.spreadsheets().values().batchUpdate(spreadsheetId=self.sheet_id, body=body).execute()
        return result
