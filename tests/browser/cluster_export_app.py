"""Owned dashboard with a small cluster-export page for browser acceptance."""
from process_logs_app import app
from server import event_log_trace_clusters

event_log_trace_clusters.MAX_EXPORT_CASES = 2
