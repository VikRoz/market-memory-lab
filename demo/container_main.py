"""Explicit container entrypoint; no model account, login or model generation."""
from demo.database import main

if __name__ == '__main__':
    raise SystemExit(main())
