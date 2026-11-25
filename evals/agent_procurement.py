"""Development CLI compatibility; production comparison lives in shared."""
from shared.agent_procurement import compare, digest, _load, main

if __name__ == "__main__":
    raise SystemExit(main())
