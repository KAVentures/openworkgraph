"""Compatibility imports for the shared process-catalog implementation."""
from shared.process_catalog import (
    CatalogWrite, ProcessCatalog, ProcessID, process_bpmn, process_document,
)

__all__ = ["CatalogWrite", "ProcessCatalog", "ProcessID", "process_bpmn", "process_document"]
