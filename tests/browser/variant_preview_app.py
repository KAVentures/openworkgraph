"""Synthetic small display budget for real-browser truncation tests."""
from process_logs_app import app
from server import event_log_variant_preview

event_log_variant_preview.MAX_PATH = 3
