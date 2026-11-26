"""SharePoint as the kernel's document base, through Microsoft Graph."""

from guilda_sharepoint.connector import SharePointConnector
from guilda_sharepoint.credentials import ClientCertificate, ClientSecret, DelegatedToken

__all__ = ["ClientCertificate", "ClientSecret", "DelegatedToken", "SharePointConnector"]
