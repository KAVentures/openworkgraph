"""Development CLI compatibility; production trials live in shared."""
from shared.agent_procurement_trials import freeze, review, _latency, main

if __name__ == "__main__":
    raise SystemExit(main())
