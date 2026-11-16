"""SharePoint as the kernel's document base, through Microsoft Graph."""

from pxos_sharepoint.connector import SharePointConnector
from pxos_sharepoint.credentials import ClientCertificate, ClientSecret

__all__ = ["ClientCertificate", "ClientSecret", "SharePointConnector"]
