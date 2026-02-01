from enum import Enum
from pydantic import BaseModel, Field
from typing import List, Optional

class AccountType(str, Enum):
    conto_corrente = "conto_corrente"
    carta_credito = "carta_credito"


class Transaction(BaseModel):
    date_operation: Optional[str] = Field(
        default=None,
        description="Operation date exactly as it appears in the statement (no normalization)."
    )
    date_value: Optional[str] = Field(
        default=None,
        description="Value date exactly as it appears in the statement (no normalization)."
    )
    description: str = Field(
        description="Full transaction description exactly as it appears in the statement."
    )
    amount: str = Field(
        description="Transaction amount exactly as it appears, including signs, separators, and CR/DB text."
    )
    currency: Optional[str] = Field(
        default=None,
        description="Currency code or symbol if present (e.g. EUR, €, USD), otherwise null."
    )


class BankStatementPage(BaseModel):
    iban_or_card: Optional[str] = Field(
        default=None,
        description="IBAN or card number associated with the statement, if present."
    )
    account_type: Optional[AccountType] = Field(
        default=None,
        description="Type of account. Must be either 'conto_corrente' or 'carta_credito' if identifiable."
    )
    transaction: List[Transaction] = Field(
        description="List of extracted transactions. Empty list if no valid transaction table is present."
    )

# esempio di utilizzo:
# llm = llm.with_structured_output(BankStatementPage)
# bank_statement_page = llm.invoke(messages)
